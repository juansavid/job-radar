"""job-radar: junta ofertas de fuentes públicas, las filtra, las puntúa y las guarda."""

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
import time
import tomllib
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).parent
USER_AGENT = "job-radar/0.1 (personal job search)"
HEADERS = [
    "Encontrada", "Círculo", "Puntaje", "Empresa", "Puesto", "Ubicación", "Encaje",
    "Alertas", "Salario", "Publicada", "Fuente", "Link", "Estado", "Comentarios", "ID",
]
ARGENTINA = ["argentina", "buenos aires", "caba"]
LATAM = ["latam", "latin america", "south america", "latinoamerica", "latinoamérica", "américa latina"]
GLOBAL = ["worldwide", "anywhere", "global", "work from anywhere"]
AMBIGUOUS_WORDS = {"remote", "remoto", "hybrid", "home", "wfh", "americas", "from", "work"}
RESTRICTED = re.compile(
    r"(must|need to|required to|should)\s+(be\s+)?(based|located|reside|live)\s+in\s+(the\s+)?"
    r"(u\.?s\.?a?\b|united states|canada|uk\b|united kingdom|europe|eu\b)"
    r"|authori[sz]ed to work in (the\s+)?(u\.?s\.?\b|united states|canada|uk\b)"
    r"|\b(us|usa|u\.s\.)[- ]only\b",
    re.IGNORECASE,
)


@dataclass
class Job:
    source: str
    company: str
    title: str
    location: str
    url: str
    description: str = ""
    salary: str = ""
    posted: object = None
    tag: str = ""


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def get_json(url):
    return json.loads(get(url))


def clean_html(value):
    text = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_date(value):
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            seconds = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(seconds, timezone.utc)
        text = str(value).strip()
        if text.isdigit():
            return parse_date(int(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def fetch_greenhouse(company):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{company['slug']}/jobs?content=true")
    return [
        Job("Greenhouse", company["name"], job["title"], (job.get("location") or {}).get("name", ""),
            job["absolute_url"], clean_html(job.get("content")),
            posted=job.get("first_published") or job.get("updated_at"), tag=company.get("tag", ""))
        for job in data.get("jobs", [])
    ]


def fetch_lever(company):
    data = get_json(f"https://api.lever.co/v0/postings/{company['slug']}?mode=json")
    jobs = []
    for job in data:
        categories = job.get("categories") or {}
        locations = categories.get("allLocations") or [categories.get("location", "")]
        jobs.append(Job(
            "Lever", company["name"], job["text"], "; ".join(filter(None, locations)), job["hostedUrl"],
            job.get("descriptionPlain", ""), posted=job.get("createdAt"), tag=company.get("tag", ""),
        ))
    return jobs


def fetch_ashby(company):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{company['slug']}")
    jobs = []
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue
        others = [item.get("location", "") for item in job.get("secondaryLocations") or []]
        location = "; ".join(filter(None, [job.get("location", ""), *others]))
        jobs.append(Job(
            "Ashby", company["name"], job["title"], location, job["jobUrl"],
            job.get("descriptionPlain", ""), posted=job.get("publishedAt"), tag=company.get("tag", ""),
        ))
    return jobs


def fetch_himalayas(terms, pages=2):
    jobs = []
    for term in terms:
        for page in range(1, pages + 1):
            query = urllib.parse.urlencode({"q": term, "page": page})
            results = get_json(f"https://himalayas.app/jobs/api/search?{query}").get("jobs", [])
            for job in results:
                low, high = job.get("minSalary"), job.get("maxSalary")
                salary = ""
                if low or high:
                    salary = f"{low or '?'}-{high or '?'} {job.get('currency') or ''} {job.get('salaryPeriod') or ''}".strip()
                jobs.append(Job(
                    "Himalayas", job.get("companyName", ""), job.get("title", ""),
                    "; ".join(job.get("locationRestrictions") or []),
                    job.get("applicationLink") or job.get("guid", ""), clean_html(job.get("description")),
                    salary=salary, posted=job.get("pubDate"),
                ))
            if len(results) < 20:
                break
            time.sleep(0.3)
    return jobs


def fetch_remotive():
    data = get_json("https://remotive.com/api/remote-jobs")
    return [
        Job("Remotive", job.get("company_name", ""), job.get("title", ""),
            job.get("candidate_required_location", ""), job.get("url", ""),
            clean_html(job.get("description")), salary=job.get("salary") or "",
            posted=job.get("publication_date"))
        for job in data.get("jobs", [])
    ]


def fetch_remoteok():
    data = get_json("https://remoteok.com/api")
    return [
        Job("Remote OK", job.get("company", ""), job.get("position", ""), job.get("location", ""),
            job.get("url", ""), clean_html(job.get("description")), posted=job.get("date"))
        for job in data
        if job.get("position")
    ]


def fetch_jobicy():
    jobs = []
    for geo in ("latam", "argentina"):
        data = get_json(f"https://jobicy.com/api/v2/remote-jobs?count=50&geo={geo}")
        for job in data.get("jobs", []):
            jobs.append(Job(
                "Jobicy", job.get("companyName", ""), html.unescape(job.get("jobTitle", "")),
                job.get("jobGeo", ""), job.get("url", ""), clean_html(job.get("jobDescription")),
                posted=job.get("pubDate"),
            ))
    return jobs


def fetch_weworkremotely():
    root = ET.fromstring(get("https://weworkremotely.com/remote-jobs.rss"))
    jobs = []
    for item in root.iter("item"):
        company, _, title = (item.findtext("title") or "").partition(":")
        jobs.append(Job(
            "We Work Remotely", company.strip(), title.strip(), item.findtext("region") or "",
            item.findtext("link") or "", clean_html(item.findtext("description")),
            posted=item.findtext("pubDate"),
        ))
    return jobs


def has_term(text, terms):
    return any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE) for term in terms)


def classify_location(location, description=""):
    """Devuelve Argentina, LATAM, Global o Revisar; None si el puesto no se puede tomar."""
    if has_term(location, ARGENTINA):
        return "Argentina"
    if has_term(location, LATAM):
        return "LATAM"
    if has_term(location, GLOBAL):
        return "Global"
    words = set(re.findall(r"[^\W\d_]+", location.lower()))
    if words - AMBIGUOUS_WORDS:
        return None
    return None if RESTRICTED.search(description) else "Revisar"


def match_circle(job, config):
    if has_term(job.title, config["exclude_titles"]):
        return None
    text = f"{job.title} {job.description}"
    for circle in config["circles"]:
        if not has_term(job.title, circle["titles"]):
            continue
        if has_term(text, circle.get("exclude_text", [])):
            continue
        requires = circle.get("requires")
        if requires == "adtech" and job.tag != "adtech" and not has_term(text, config["adtech_terms"]):
            continue
        if requires == "betting" and not has_term(text, config["betting_terms"]):
            continue
        return circle
    return None


def alerts_for(job, fit, config):
    alerts = []
    if has_term(job.description, config["sales_alerts"]):
        alerts.append("Posible objetivo comercial")
    if has_term(f"{job.location} {job.description}", config["onsite_alerts"]):
        alerts.append("Posible presencial o híbrido")
    if fit == "Revisar":
        alerts.append("Confirmar si contrata desde Argentina")
    return alerts


def score_job(job, circle, fit, alerts, config):
    text = f"{job.title} {job.description}"
    skills = sum(points for term, points in config["skills"].items() if has_term(text, [term]))
    score = circle["base"] + min(skills, 30) + config["location_points"][fit]
    if has_term(job.title, ["manager", "lead", "senior", "sr", "director", "head", "principal"]):
        score += 5
    if "Posible objetivo comercial" in alerts:
        score -= 10
    return score


def job_id(job):
    normalized = re.sub(r"\W+", " ", f"{job.company}|{job.title}".lower()).strip()
    return hashlib.sha1(normalized.encode()).hexdigest()[:16]


def evaluate(jobs, config, now=None):
    """Filtra y puntúa. Si una oferta aparece en varias fuentes, se queda con la de mayor puntaje."""
    now = now or datetime.now(timezone.utc)
    best = {}
    for job in jobs:
        posted = parse_date(job.posted)
        if posted and (now - posted).days > config["max_age_days"]:
            continue
        circle = match_circle(job, config)
        if not circle:
            continue
        fit = classify_location(job.location, job.description)
        if not fit:
            continue
        alerts = alerts_for(job, fit, config)
        score = score_job(job, circle, fit, alerts, config)
        if score < config["min_score"]:
            continue
        row = {
            "Encontrada": now.date().isoformat(),
            "Círculo": circle["name"],
            "Puntaje": score,
            "Empresa": job.company,
            "Puesto": job.title,
            "Ubicación": job.location or "Sin especificar",
            "Encaje": fit,
            "Alertas": "; ".join(alerts),
            "Salario": job.salary,
            "Publicada": posted.date().isoformat() if posted else "",
            "Fuente": job.source,
            "Link": job.url,
            "Estado": "Por revisar",
            "Comentarios": "",
            "ID": job_id(job),
        }
        if row["ID"] not in best or score > best[row["ID"]]["Puntaje"]:
            best[row["ID"]] = row
    return sorted(best.values(), key=lambda row: row["Puntaje"], reverse=True)


def collect(config):
    fetchers = [
        ("Himalayas", lambda: fetch_himalayas(config["search_terms"])),
        ("Remotive", fetch_remotive),
        ("Remote OK", fetch_remoteok),
        ("Jobicy", fetch_jobicy),
        ("We Work Remotely", fetch_weworkremotely),
    ]
    by_ats = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}
    for company in config["companies"]:
        fetchers.append((company["name"], lambda c=company: by_ats[c["ats"]](c)))

    jobs, errors = [], []
    for name, fetch in fetchers:
        try:
            found = fetch()
            jobs.extend(found)
            print(f"{name}: {len(found)}")
        except Exception as error:
            errors.append(name)
            print(f"{name}: ERROR {error}", file=sys.stderr)
        time.sleep(0.2)
    return jobs, errors, len(fetchers)


def summary_row(rows, analyzed, errors, config):
    names = list(dict.fromkeys(circle["name"] for circle in config["circles"]))
    counts = [sum(row["Círculo"] == name for row in rows) for name in names]
    today = datetime.now(timezone.utc).date().isoformat()
    headers = ["Fecha", "Analizadas", "Nuevas", *names, "Fuentes con error"]
    return headers, [today, analyzed, len(rows), *counts, ", ".join(errors)]


def save_to_sheet(rows, summary_headers, summary_values, config):
    import gspread

    credentials = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    spreadsheet = gspread.service_account_from_dict(credentials).open_by_key(os.environ["GOOGLE_SHEET_ID"])

    def worksheet(title, headers):
        try:
            sheet = spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(title, rows=1000, cols=len(headers))
        if not sheet.row_values(1):
            sheet.update([headers], "A1")
            sheet.freeze(rows=1)
        return sheet

    sheet = worksheet(config["sheet_tab"], HEADERS)
    # Usa los encabezados actuales para no pisar columnas que hayas movido o agregado.
    current = sheet.row_values(1)
    if "ID" not in current:
        raise RuntimeError("La hoja no tiene la columna ID; volvé a agregarla para evitar duplicados.")
    seen = set(sheet.col_values(current.index("ID") + 1)[1:])
    new = [row for row in rows if row["ID"] not in seen]
    if new:
        # RAW evita que un texto de una oferta se ejecute como fórmula.
        sheet.append_rows(
            [[row.get(header, "") for header in current] for row in new],
            value_input_option="RAW",
        )
    summary_values[2:] = recount(new, summary_headers, config) + [summary_values[-1]]
    worksheet(config["summary_tab"], summary_headers).append_row(summary_values, value_input_option="RAW")
    return len(new)


def recount(rows, headers, config):
    names = headers[3:-1]
    return [len(rows), *[sum(row["Círculo"] == name for row in rows) for name in names]]


def save_to_csv(rows, path):
    path.parent.mkdir(exist_ok=True)
    seen = set()
    if path.exists():
        with path.open(newline="", encoding="utf-8") as file:
            seen = {row["ID"] for row in csv.DictReader(file)}
    new = [row for row in rows if row["ID"] not in seen]
    is_new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=HEADERS)
        if is_new_file:
            writer.writeheader()
        writer.writerows(new)
    return len(new)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="muestra los resultados sin guardarlos")
    args = parser.parse_args()

    config = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    jobs, errors, total_sources = collect(config)
    if len(errors) == total_sources:
        sys.exit("Fallaron todas las fuentes; no se guardó nada.")

    rows = evaluate(jobs, config)
    print(f"\nAnalizadas: {len(jobs)} | Coinciden: {len(rows)} | Fuentes con error: {len(errors)}")
    if args.dry_run:
        for row in rows[:40]:
            print(f"{row['Puntaje']:>3} | {row['Círculo']} | {row['Empresa']} | {row['Puesto']} | {row['Encaje']} | {row['Link']}")
        return

    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        headers, values = summary_row(rows, len(jobs), errors, config)
        added = save_to_sheet(rows, headers, values, config)
        print(f"Nuevas en Google Sheet: {added}")
    elif os.environ.get("GITHUB_ACTIONS"):
        sys.exit("Faltan los secretos de Google en GitHub.")
    else:
        added = save_to_csv(rows, ROOT / "output" / "jobs.csv")
        print(f"Nuevas en output/jobs.csv: {added}")


if __name__ == "__main__":
    main()
