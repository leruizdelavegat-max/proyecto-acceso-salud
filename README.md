# La hora dorada — acceso a centros de salud resolutivos

Análisis geoespacial del tiempo de acceso por carretera a centros de
salud resolutivos (categoría II-1 en adelante) en tres departamentos
de Perú: costero, andino y amazónico (ver `config.md`).

## Estructura del repositorio

- `config.md` — todos los parámetros del proyecto (departamentos, rutas, umbrales, motor de enrutamiento)
- `requirements.txt` — dependencias de Python
- `Fase1_Adquisicion_y_Validacion.ipynb` — Fase 1: descarga, limpieza/validación y recorte por departamento (RENIPRESS, SIGMED, límites administrativos)
- `src/routing.py` — Fase 2: motor de enrutamiento + caché de matriz de tiempos
- `src/metrics.py` — Fase 3: cálculo de métricas de acceso
- `src/export.py` — tablas y figuras para el informe
- `app.py` — Fase 4: dashboard Streamlit
- `report/main.tex` — informe final en LaTeX
- `_data/` — data provista para el curso (nunca se modifica; ver más abajo)
- `data/raw/` — datos crudos (copiados de `_data/` o descargados; no se modifican)
- `data/processed/` — datos limpios y validados (GeoPackage)
- `data/outputs/` — resultados finales (incl. matriz de tiempos precalculada)
- `logs/` — registros de ejecución e informe de calidad de datos

## Configuración inicial

```bash
python3 -m venv .venv
source .venv/bin/activate       # En Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Cómo correr la Fase 1

```bash
jupyter lab Fase1_Adquisicion_y_Validacion.ipynb
```

Correr todas las celdas en orden (Run All). El notebook vive en la raíz del
repo y todas sus rutas son relativas a ella. Las fases siguientes (Fase 2 en
adelante) siguen siendo scripts en `src/`:

```bash
python src/routing.py         # Fase 2: calcula y cachea la matriz de tiempos
python src/metrics.py         # Fase 3: calcula métricas de acceso por departamento
python src/export.py          # Genera tablas y figuras para el informe
```

## Cómo correr el dashboard

```bash
streamlit run app.py
```

## Datos declarados

- RENIPRESS/SUSALUD: https://www.datosabiertos.gob.pe/dataset/registro-nacional-de-entidades-prestadoras-de-servicios-de-salud-renipress
- SIGMED/MINEDU: https://sigmed.minedu.gob.pe/descargas/
- Red vial OSM Perú: https://download.geofabrik.de/south-america/peru-latest.osm.pbf
- Límites administrativos: [DECLARAR FUENTE — ver config.md]

## Estado de la Fase 1 (adquisición y validación)

Todo el código de la Fase 1 (adquisición + validación) vive, sin depender de
`src/`, en `Fase1_Adquisicion_y_Validacion.ipynb`. Ya viene ejecutado con
outputs reales guardados en el propio notebook.

`_data/` (provisto para el curso) contiene una copia de las 4 fuentes:
`RENIPRESS_30-04-2026.csv`, `CP_P.shp` (centros poblados), `DEPARTAMENTO.gpkg`
/ `PROVINCIA.gpkg` / `DISTRITO.gpkg` (límites INEI) y `peru-260907.osm.pbf`.
`_data/` nunca se modifica: `config.md` declara cada uno como `cache_local`
de su fuente correspondiente, y la celda de adquisición del notebook copia
de ahí a `data/raw/` (la ruta que de verdad se valida) la primera vez que se
corre — si el archivo ya existe en `data/raw/`, no se vuelve a copiar
(`FORZAR_DESCARGA = True` en esa celda para forzarlo). Si mañana falta un
archivo en `_data/`, el notebook cae automáticamente a:

- **RENIPRESS**: busca en la página del dataset el CSV mensual más reciente
  (`RENIPRESS_DD-MM-AAAA.csv`) y lo descarga. Nota: `datosabiertos.gob.pe`
  bloquea (HTTP 418) clientes sin user-agent de navegador — ya se envía uno
  (ver `config.md:descargas.user_agent`).
- **SIGMED** y **límites administrativos**: son portales interactivos sin
  enlace de descarga directa conocido; si no hay `cache_local` ni archivo ya
  en `data/raw/`, se imprimen instrucciones de descarga manual en vez de
  fallar en silencio.
- **OSM Perú**: descarga directa desde Geofabrik.

Mientras no exista la capa de distrito en `data/raw/limites_distrito.gpkg`,
la regla de "puntos fuera de su polígono distrital" queda documentada como
"no evaluado" en el informe de calidad — nunca se omite en silencio.

El informe de calidad de datos (cuántos registros marcó cada regla, qué se
hizo y por qué) se genera en `logs/reporte_calidad_datos.md` / `.json` cada
vez que se corre el notebook.
