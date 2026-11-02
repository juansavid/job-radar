# job-radar

Busca ofertas dos veces por semana (lunes y jueves, 8:00 de Argentina) y agrega las nuevas a un Google Sheet. No se postula a nada y no usa Claude al correr.

## Qué archivos tocar

- `config.toml`: búsquedas, empresas, círculos, exclusiones y puntajes.
- `radar.py`: el programa.

## Fuentes

Himalayas, Remotive, Remote OK, Jobicy, We Work Remotely y las páginas de empleo de las empresas de `config.toml`. No hace scraping de LinkedIn: para eso usá las alertas de empleo de LinkedIn por mail.

## Probarlo en la compu

Requiere Python 3.11 o superior. Para las pruebas locales no hace falta instalar `requirements.txt`.

Sin guardar nada:

```bash
python3 radar.py --dry-run
```

Guardando en `output/jobs.csv`:

```bash
python3 radar.py
```

Tests:

```bash
python3 -m unittest
```

## Conectarlo a Google Sheets y GitHub

1. Crear un repositorio privado en GitHub y subir esta carpeta.
2. En Google Cloud, habilitar las APIs de Google Sheets y Google Drive, y crear una cuenta de servicio con una clave JSON.
3. Crear un Google Sheet y compartirlo como editor con el mail de esa cuenta de servicio.
4. En GitHub, ir a Settings > Secrets and variables > Actions y crear:
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: el contenido de la clave JSON.
   - `GOOGLE_SHEET_ID`: la parte del link del Sheet entre `/d/` y `/edit`.
5. En la pestaña Actions, correr `job-radar` a mano. Con `dry_run` activado muestra los resultados sin guardarlos; desactivalo para probar que escriba en el Sheet.

Nunca subas la clave JSON al repositorio.

## El Sheet

- **Ofertas:** podés editar `Estado` y `Comentarios`, y agregar columnas. El programa solo agrega filas nuevas. No borres la columna `ID`, porque evita duplicados.
- **Resumen:** registra cuántas ofertas nuevas aparecen por círculo en cada corrida, para ver el tamaño real de cada mercado.
