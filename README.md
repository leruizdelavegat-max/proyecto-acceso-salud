# La hora dorada — acceso a centros de salud resolutivos

Análisis geoespacial del tiempo de acceso por carretera a centros de
salud resolutivos (categoría II-1 en adelante) en tres departamentos
de Perú: costero, andino y amazónico (ver `config.md`).

## Estructura del repositorio

- `config.md` — todos los parámetros del proyecto (departamentos, rutas, umbrales, motor de enrutamiento)
- `requirements.txt` — dependencias de Python
- `Fase1_Adquisicion_y_Validacion.ipynb` — Fase 1: descarga, limpieza/validación, recorte por departamento (RENIPRESS, SIGMED, límites administrativos) y mapas interactivos Folium (demanda vs. oferta resolutiva)
- `src/routing.py` — Fase 2: grafo OSM por departamento (OSMnx), snapping, muestreo, matriz de tiempos (NetworkX) y caché
- `tests/test_routing.py` — pruebas de las funciones puras de la Fase 2 (sin red)
- `src/poblacion.py` — Fase 3 (insumo): población por centro poblado (raster WorldPop)
- `src/metrics.py` — Fase 3: métricas de acceso (funciones DataFrame→DataFrame)
- `src/export.py` — Fase 5: figuras (`report/figures/`), tablas (`report/tables/`) y cifras del informe
- `app.py` — Fase 4: dashboard Streamlit (lee solo archivos precalculados)
- `report/main.tex` / `report/main.pdf` — informe final en LaTeX (fuente + compilado)
- `tests/` — pruebas de las funciones puras (`pytest -q tests/`)
- `_data/` — data provista para el curso (nunca se modifica; ver más abajo)
- `data/raw/` — datos crudos (copiados de `_data/` o descargados; no se modifican)
- `data/processed/` — datos limpios y validados (GeoPackage)
- `data/outputs/` — resultados finales: mapas interactivos (`mapa_acceso_<depto>.html`) y la matriz de tiempos precalculada de la Fase 2 (`matriz_tiempos.parquet`)
- `data/interim/` — grafos OSM cacheados + caché de Overpass (ignorado por git; se regenera solo)
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
repo y todas sus rutas son relativas a ella. Las fases siguientes siguen
siendo scripts en `src/`:

```bash
python src/poblacion.py       # Fase 3 (insumo): población por centro poblado (WorldPop)
python src/metrics.py         # Fase 3: métricas de acceso -> data/outputs/*.csv + report/tables/*.tex
python src/export.py          # Genera figuras para el informe
```

## Cómo correr la Fase 2 (ruteo — pyosmium + NetworkX)

Motor: **grafo NetworkX construido desde el `.pbf` local con `pyosmium`**
(`config.md → routing.motor: networkx`, `grafo.fuente: pbf_local`).

Decisiones y obstáculos (para el video):
- Se **descartó OSRM en Docker** (la opción preferida del enunciado): la BIOS
  de la máquina tiene la virtualización desactivada y bloqueada por IT, así
  que WSL2/Docker no arrancan (`WslRegisterDistribution 0x80370102`).
- Se **descartó `pyrosm`**: no compila en Python 3.14 sin MSVC Build Tools.
- Se **descartó OSMnx/Overpass**: el servidor público `overpass-api.de`
  estaba inalcanzable (timeout) el día de la corrida, y a escala
  departamental Overpass parte la consulta en decenas de sub-peticiones.
- Solución: leer la red vial directamente del `data/raw/peru-latest.osm.pbf`
  de la Fase 1 con `pyosmium` (offline, reproducible). Queda `osmnx_overpass`
  como fallback conmutable en `config.md`.

**1. Dependencias**:

```bash
pip install -r requirements.txt      # osmium (pyosmium), networkx, scipy, pyarrow
```

**2. Prueba rápida** (lee el `.pbf`, arma el grafo del departamento más chico
y rutea ~30 centros poblados; sin red):

```bash
python src/routing.py --smoke
```

**3. Correr el ruteo** — empezar por el departamento más chico (checkpoint):

```bash
python src/routing.py --departamento ucayali     # 1 depto (~5 min)
python src/routing.py --all                       # los 3 (~25-45 min la 1ª vez)
python src/routing.py --all --perfil car          # solo un perfil si falta RAM/tiempo
```

Notas de rendimiento (probado en un equipo con 8 GB de RAM):
- El `.pbf` de Perú se escanea **una sola vez** por corrida (~2 min) y las
  vías quedan en memoria; cada grafo (departamento × perfil) se **simplifica**
  (colapsa los puntos-forma: ~18× menos nodos) y se cachea en
  `data/interim/graphs/*.pkl`. Re-correr carga los `.pkl` en segundos.
- La matriz se acumula en `data/outputs/matriz_tiempos.parquet`; los pares ya
  calculados no se recomputan. `--forzar` ignora todas las cachés.
- **Cierra Chrome y apps pesadas antes del primer `--all`.** Si aun así falta
  RAM: córrelo por perfil (`--perfil car`, luego `bike`, luego `foot`) o por
  departamento, o baja `routing.muestreo.tope_puntos_demanda` en `config.md`.
- El entregable es el Parquet: una vez calculado (aquí o en otra máquina /
  Colab), se commitea y el dashboard de la Fase 4 corre sin volver a rutear.

Salidas:

| Archivo | Contenido |
|---|---|
| `data/outputs/matriz_tiempos.parquet` | matriz **completa** origen × instalación para el perfil `car`, con destinos = resolutivos + I-3 + I-4 (candidatos a upgrade en la Fase 4). Para bike/foot: solo el resolutivo más cercano por origen. |
| `data/outputs/acceso_nearest.parquet` | por centro poblado y perfil: instalación más cercana, tiempo, distancia, `routable` |
| `data/outputs/routing_snap.parquet` | nodo de enganche de cada punto (demanda y oferta), distancia de snap, `snap_ok` |
| `data/processed/demanda_muestreada.gpkg` | los ≤ 5 000 centros poblados ruteados (muestreo estratificado por distrito) |
| `logs/snapping_report.csv` | por perfil y tipo: puntos que no engancharon y distancia media/p95/máx de snap |
| `logs/routing_run.log` | log de ejecución (tiempo transcurrido, progreso, comparación car-vs-foot) |

Puntos sin ruta (isla, componente desconectada) se marcan `routable=False`;
**no** se les asigna distancia en línea recta.

Pruebas de las funciones puras (no necesitan red):

```bash
pytest -q tests/test_routing.py
```

## Cómo correr la Fase 3 (métricas)

```bash
python src/poblacion.py    # descarga WorldPop 1 km y estima población por centro poblado
python src/metrics.py      # calcula todas las métricas
pytest -q tests/test_metrics.py
```

**Población**: SIGMED no trae población. `src/poblacion.py` la estima del
raster **WorldPop 2020, 1 km, UN-ajustado** (no *constrained*), asignando
cada celda al centro poblado más cercano *dentro del polígono de su
departamento* — así el total por departamento coincide con el censo
(Piura ≈ 1.86 M, Ayacucho ≈ 0.74 M, Ucayali ≈ 0.57 M). Sale a
`data/processed/centros_poblados_poblacion.parquet`.

`src/metrics.py` produce (cada métrica = función DataFrame→DataFrame, sin
lógica en el dashboard):

| Salida (`data/outputs/`) | Contenido |
|---|---|
| `metricas_por_centro_poblado.csv` | `t_min(i)`, distancia, banda, población, urbano/rural, altitud por centro poblado |
| `cobertura_bandas.csv` / `..._por_departamento.csv` | % de población a <30 / 30-60 / 60-120 / >120 min y sin ruta |
| `acceso_ponderado_{distrito,provincia,departamento}.csv` | acceso medio **ponderado por población** por nivel |
| `brechas_criticas_distritos.csv` | distritos con peor acceso ponderado, rankeados |
| `desigualdad_gini.csv` + `curva_lorenz.csv` | Gini y curva de Lorenz del acceso ponderado |
| `contraste_urbano_rural.csv` | acceso y cobertura urbano vs rural (regla INEI: ≥2000 hab. o capital) |
| `cross_acceso_altitud.csv` | cross-análisis acceso × altitud (correlacional, no causal) |

Tablas LaTeX en `report/tables/*.tex` (generadas, no escritas a mano).

## Cómo correr el dashboard (Fase 4)

```bash
streamlit run app.py
```

Lee **solo** archivos precalculados (`data/outputs/*.parquet`, `*.csv`,
`data/processed/*.gpkg`, `logs/quality_report.csv`) — no rutea ni construye
grafos. Vistas: KPIs · mapa coroplético de acceso por distrito + capa de
establecimientos filtrable por categoría e institución · ECDF del tiempo de
acceso (urbano/rural o por depto) · tabla de distritos con descarga CSV ·
**simulador de escenario** (subir establecimientos I-3/I-4 a resolutivos y
ver la ganancia de cobertura, usando la matriz de la Fase 2) · panel de
calidad de datos de la Fase 1.

## Cómo generar el informe (Fase 5)

```bash
python src/export.py            # figuras (report/figures/*.pdf) + tablas
                                # (report/tables/*.tex) + cifras (_numeros.tex)
cd report && pdflatex main.tex && pdflatex main.tex
```

`report/main.tex` se rellena solo desde `report/tables/_numeros.tex` (cifras)
y `\input`/`\includegraphics` de las tablas y figuras generadas por el
pipeline — ninguna es captura del dashboard. El PDF compilado
(`report/main.pdf`) se commitea junto con el fuente.

## Datos declarados

- RENIPRESS/SUSALUD: https://www.datosabiertos.gob.pe/dataset/registro-nacional-de-entidades-prestadoras-de-servicios-de-salud-renipress
- SIGMED/MINEDU: https://sigmed.minedu.gob.pe/descargas/
- Red vial OSM Perú: https://download.geofabrik.de/south-america/peru-latest.osm.pbf
- Límites administrativos: [DECLARAR FUENTE — ver config.md]

## Estado de la Fase 1 (adquisición y validación)

Todo el código de la Fase 1 vive, sin depender de `src/`, en
`Fase1_Adquisicion_y_Validacion.ipynb`, ejecutado y con outputs guardados.
El notebook sigue 5 pasos:

1. **Adquisición / carga** — si el archivo ya está en `data/raw/` se usa tal
   cual; si no, se copia de `_data/` o se descarga; luego se lee a memoria con
   `pandas` / `geopandas` detectando el encoding (`utf-8` / `latin-1`, `chardet`).
2. **Limpieza y validación espacial** — estandariza texto de categorías
   (`II-1`) y estados (`ACTIVO`); valida coordenadas (vacías/cero, signo de
   hemisferio, intercambio lat/lon, fuera de Perú); marca duplicados y puntos
   fuera de su polígono distrital. Todo se cuantifica en
   `logs/quality_report.csv` (+ `quality_report.md`).
3. **Filtrado de ámbito** — recorta oferta y demanda a los 3 departamentos de
   `config.md`.
4. **Almacenamiento** — GeoPackage en `data/processed/` (capa nacional + un
   recorte por departamento).
5. **Exploración con Folium** — un mapa interactivo por departamento en
   `data/outputs/mapa_acceso_<depto>.html`.

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
