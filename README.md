# La hora dorada — acceso a centros de salud resolutivos

Análisis geoespacial del tiempo de acceso por carretera a centros de
salud resolutivos (categoría II-1 en adelante) en tres departamentos
de Perú: costero, andino y amazónico (ver `config.md`).

## Estructura del repositorio

- `config.md` — todos los parámetros del proyecto (departamentos, rutas, umbrales, motor de enrutamiento)
- `requirements.txt` — dependencias de Python
- `src/acquisition.py` — Fase 1: descarga de datos (RENIPRESS, SIGMED, OSM, límites administrativos)
- `src/validation.py` — Fase 1: reglas de calidad de datos
- `src/routing.py` — Fase 2: motor de enrutamiento + caché de matriz de tiempos
- `src/metrics.py` — Fase 3: cálculo de métricas de acceso
- `src/export.py` — tablas y figuras para el informe
- `app.py` — Fase 4: dashboard Streamlit
- `report/main.tex` — informe final en LaTeX
- `data/raw/` — datos crudos descargados (no se modifican)
- `data/processed/` — datos limpios y validados
- `data/outputs/` — resultados finales (incl. matriz de tiempos precalculada)
- `logs/` — registros de ejecución e informe de calidad de datos

## Configuración inicial

```bash
python3 -m venv .venv
source .venv/bin/activate       # En Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Pasos para descargar y procesar los datos

```bash
python src/acquisition.py     # Descarga las 4 fuentes (idempotente)
python src/validation.py      # Limpia y valida, genera reporte en logs/
python src/routing.py         # Calcula y cachea la matriz de tiempos
python src/metrics.py         # Calcula métricas de acceso por departamento
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

`_data/` (provisto para el curso) contiene una copia de las 4 fuentes:
`RENIPRESS_30-04-2026.csv`, `CP_P.shp` (centros poblados), `DEPARTAMENTO.gpkg`
/ `PROVINCIA.gpkg` / `DISTRITO.gpkg` (límites INEI) y `peru-260907.osm.pbf`.
`_data/` nunca se modifica: `config.md` declara cada uno como `cache_local`
de su fuente correspondiente, y `acquisition.py` copia de ahí a `data/raw/`
(la ruta que de verdad lee `validation.py`) la primera vez que se corre.
Si mañana falta un archivo en `_data/` o quieres forzar la descarga real,
`acquisition.py` cae automáticamente a:

- **RENIPRESS**: busca en la página del dataset el CSV mensual más reciente
  (`RENIPRESS_DD-MM-AAAA.csv`) y lo descarga. Nota: `datosabiertos.gob.pe`
  bloquea (HTTP 418) clientes sin user-agent de navegador — el script ya
  envía uno (ver `config.md:descargas.user_agent`).
- **SIGMED** y **límites administrativos**: son portales interactivos sin
  enlace de descarga directa conocido; si no hay `cache_local` ni archivo ya
  en `data/raw/`, el script imprime instrucciones de descarga manual en vez
  de fallar en silencio.
- **OSM Perú**: descarga directa desde Geofabrik.

Mientras no exista la capa de distrito en `data/raw/limites_distrito.gpkg`,
la regla de "puntos fuera de su polígono distrital" queda documentada como
"no evaluado" en el informe de calidad — nunca se omite en silencio.

El informe de calidad de datos (cuántos registros marcó cada regla, qué se
hizo y por qué) se genera en `logs/reporte_calidad_datos.md` / `.json` cada
vez que corres `validation.py`.
