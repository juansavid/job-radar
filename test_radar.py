import tomllib
import unittest
from datetime import datetime, timezone
from pathlib import Path

from radar import Job, classify_location, evaluate, job_id, match_circle, parse_date

CONFIG = tomllib.loads((Path(__file__).parent / "config.toml").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)


def job(title, location="Remote - LATAM", description="", company="Acme", tag="", posted=None):
    return Job("Test", company, title, location, "https://example.com", description, posted=posted, tag=tag)


class LocationTests(unittest.TestCase):
    def test_accepted_locations(self):
        self.assertEqual(classify_location("Buenos Aires, Argentina"), "Argentina")
        self.assertEqual(classify_location("Remote - LATAM"), "LATAM")
        self.assertEqual(classify_location("Anywhere in the World"), "Global")
        self.assertEqual(classify_location("United States; Argentina"), "Argentina")

    def test_ambiguous_locations_need_review(self):
        self.assertEqual(classify_location("Remote"), "Revisar")
        self.assertEqual(classify_location(""), "Revisar")

    def test_restricted_locations_are_rejected(self):
        self.assertIsNone(classify_location("USA Only"))
        self.assertIsNone(classify_location("US - Remote"))
        self.assertIsNone(classify_location("Toronto, Canada"))
        self.assertIsNone(classify_location("Remote", "You must be based in the United States."))


class CircleTests(unittest.TestCase):
    def test_core_role(self):
        self.assertIn("Núcleo", match_circle(job("Ad Operations Manager"), CONFIG)["name"])

    def test_sales_and_revops_are_excluded(self):
        self.assertIsNone(match_circle(job("Programmatic Sales Manager"), CONFIG))
        self.assertIsNone(match_circle(job("Revenue Operations Manager"), CONFIG))
        self.assertIsNone(match_circle(job("Account Executive, AdTech"), CONFIG))

    def test_non_operations_roles_are_excluded(self):
        self.assertIsNone(match_circle(job("Frontend Engineer, Ads Campaign Manager"), CONFIG))
        self.assertIsNone(match_circle(job("Senior Product Manager, Retail Media"), CONFIG))
        self.assertIsNone(match_circle(job("Data Scientist, Commerce Media"), CONFIG))

    def test_customer_success_needs_ad_context(self):
        self.assertIsNone(match_circle(job("Customer Success Manager"), CONFIG))
        match = match_circle(job("Customer Success Manager", tag="adtech"), CONFIG)
        self.assertIn("Ad tech", match["name"])

    def test_b2b_marketing_ops_is_excluded(self):
        title = "Marketing Operations Manager"
        self.assertIsNone(match_circle(job(title, description="Own HubSpot and lead scoring."), CONFIG))
        self.assertIsNotNone(match_circle(job(title, description="Own campaign tracking."), CONFIG))


class EvaluateTests(unittest.TestCase):
    def test_skills_and_location_raise_score(self):
        rows = evaluate([
            job("Ad Operations Manager", "Argentina", "Google Ad Manager and Prebid", company="A"),
            job("Ad Operations Manager", "Remote", company="B"),
        ], CONFIG, NOW)
        self.assertEqual([row["Empresa"] for row in rows], ["A", "B"])

    def test_sales_alert_is_flagged(self):
        rows = evaluate([job("Technical Account Manager", tag="adtech", description="Own your quota.")], CONFIG, NOW)
        self.assertIn("Posible objetivo comercial", rows[0]["Alertas"])

    def test_duplicates_keep_best_score(self):
        rows = evaluate([
            job("Ad Operations Manager", "Remote"),
            job("Ad Operations Manager", "Argentina"),
        ], CONFIG, NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Encaje"], "Argentina")

    def test_old_jobs_are_dropped(self):
        self.assertEqual(evaluate([job("Ad Operations Manager", posted="2025-01-01")], CONFIG, NOW), [])

    def test_id_ignores_case_and_punctuation(self):
        self.assertEqual(job_id(job("Ad Ops, Manager")), job_id(job("ad ops manager")))


class DateTests(unittest.TestCase):
    def test_supported_formats(self):
        self.assertEqual(parse_date(1793631720).year, 2026)
        self.assertEqual(parse_date(1793631720000).year, 2026)
        self.assertEqual(parse_date("2026-07-29T16:05:42.856+00:00").month, 7)
        self.assertEqual(parse_date("Mon, 02 Nov 2026 10:00:00 +0000").day, 2)
        self.assertIsNone(parse_date("not a date"))


if __name__ == "__main__":
    unittest.main()
