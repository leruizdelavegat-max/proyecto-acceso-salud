# -*- coding: utf-8 -*-
"""
Fase 2 — Ruteo y cálculo de tiempos de viaje.

Para cada punto de demanda (centro poblado) calcula el tiempo y la distancia
por carretera hasta el establecimiento **resolutivo** más cercano, en tres
perfiles (car / bike / foot), y además el tiempo a pie hasta el
establecimiento **de cualquier categoría** más cercano.

Motor: **pyosmium + NetworkX**. La red vial se lee directamente del `.pbf`
local de Perú (un solo escaneo, con índice de nodos en disco), se arma un
grafo NetworkX, se simplifican los nodos-forma y se rutea con Dijkstra.
Decisiones/obstáculos: se descartó OSRM (BIOS con virtualización bloqueada
por IT -> Docker/WSL2 no arrancan), `pyrosm` (no compila en Python 3.14) y
OSMnx/Overpass (servidor público inalcanzable el día de la corrida). Queda
`grafo.fuente: osmnx_overpass` como fallback conmutable en config.md.

Requisitos del enunciado y cómo se cumplen:
  - Red vial real (no Haversine)          -> grafo OSM del .pbf (pyosmium)
  - Snapping + reporte                     -> snap_nodos() -> logs/snapping_report.csv
  - Caché, sin recomputar                  -> grafos en data/interim/graphs/*.pkl;
                                             matriz en data/outputs/matriz_tiempos.parquet
  - Matriz completa origen x instalación    -> perfil `car`: todos los pares contra
    (para el simulador de la Fase 4)          resolutivos + I-3 + I-4 (candidatos a upgrade)
  - Fallback documentado                    -> nodo/par sin ruta -> routable=False;
                                             NO se asigna distancia en línea recta
  - Tope de 5 000 puntos de demanda         -> muestrear_demanda() estratificado por distrito
  - Log de tiempo y progreso

CLI:
    python src/routing.py --smoke                 # arma el grafo del depto más chico y rutea 30 pts
    python src/routing.py --departamento ucayali  # checkpoint (1 depto)
    python src/routing.py --all                   # los 3 departamentos
    python src/routing.py --all --perfil car      # subconjunto de perfiles (si falta RAM)
    python src/routing.py --all --forzar          # ignora caché (grafos y matriz)

Como librería (funciones puras, sin red):
    from src.routing import muestrear_demanda, acceso_mas_cercano, haversine_m
"""
from __future__ import annotations

import argparse
import logging
import math
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

try:  # geopandas / osmnx / networkx solo hacen falta para run(), no para las puras
    import geopandas as gpd
except Exception:  # pragma: no cover
    gpd = None

REPO_ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("routing")

# perfil del proyecto -> network_type de OSMnx
_NETWORK_TYPE = {"car": "drive", "bike": "bike", "foot": "walk"}

# Velocidad de referencia (km/h) por tipo de vía OSM, para el perfil `car`
# cuando la etiqueta maxspeed no está. bike/foot usan velocidad uniforme (config).
_CAR_KMH = {
    "motorway": 90, "motorway_link": 45, "trunk": 70, "trunk_link": 40,
    "primary": 60, "primary_link": 35, "secondary": 50, "secondary_link": 30,
    "tertiary": 40, "tertiary_link": 25, "unclassified": 30, "residential": 30,
    "living_street": 15, "service": 20, "road": 30, "track": 20, "default": 30,
}


# ===========================================================================
# Configuración / logging
# ===========================================================================
def cargar_config(path: Path | str | None = None) -> dict:
    import yaml
    path = Path(path) if path else REPO_ROOT / "config.md"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ruta(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else REPO_ROOT / p


def configurar_logging(archivo_log: Path | None) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    if archivo_log:
        archivo_log.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(archivo_log, mode="w", encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)


# ===========================================================================
# Funciones puras (sin red) — testeables de forma independiente
# ===========================================================================
def _a_float2d(datos, shape: tuple[int, int]) -> np.ndarray:
    if not datos:
        return np.full(shape, np.nan)
    return np.array([[np.nan if v is None else v for v in fila] for fila in datos],
                    dtype="float64")


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Distancia en metros sobre la esfera. Solo diagnóstico / calibración,
    NUNCA como sustituto silencioso de una ruta."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def travel_time_s(length_m: float, speed_kmh: float) -> float:
    """Tiempo de viaje en segundos de una arista dada su longitud y velocidad."""
    if not speed_kmh or speed_kmh <= 0:
        return math.inf
    return float(length_m) / (float(speed_kmh) * 1000.0 / 3600.0)


def velocidad_car_kmh(highway, maxspeed) -> float:
    """Velocidad para el perfil car: usa maxspeed de OSM si es parseable,
    si no cae al valor por tipo de vía (_CAR_KMH)."""
    v = _parse_maxspeed(maxspeed)
    if v:
        return v
    hw = highway[0] if isinstance(highway, (list, tuple)) and highway else highway
    return float(_CAR_KMH.get(hw, _CAR_KMH["default"]))


def _parse_maxspeed(maxspeed) -> float | None:
    if maxspeed is None:
        return None
    if isinstance(maxspeed, (list, tuple)):
        vals = [_parse_maxspeed(x) for x in maxspeed]
        vals = [v for v in vals if v]
        return min(vals) if vals else None
    s = str(maxspeed).strip().lower()
    if s in ("none", "", "signals", "walk", "variable"):
        return None
    try:
        if "mph" in s:
            return float(s.replace("mph", "").strip()) * 1.60934
        return float(s.split()[0])
    except (ValueError, IndexError):
        return None


def muestrear_demanda(gdf, tope: int, semilla: int = 42,
                      col_distrito: str = "UBIGEO") -> tuple[object, dict]:
    """Muestreo estratificado por distrito, asignación proporcional con reparto
    de restos mayores para acertar exactamente `tope`. Determinista dada
    `semilla`. Si len(gdf) <= tope devuelve todo. No usa red."""
    n = len(gdf)
    if n <= tope:
        out = gdf.copy()
        out["en_muestra"] = True
        return out, {"n_total": n, "tope": tope, "fraccion": 1.0, "n_muestra": n,
                     "n_distritos": int(gdf[col_distrito].nunique()),
                     "estrategia": "sin_muestreo (n <= tope)", "semilla": semilla,
                     "distritos_sin_muestra": 0}

    frac = tope / n
    tam = gdf.groupby(col_distrito).size()
    cuota = tam * frac
    base = np.floor(cuota).astype(int)
    resto = int(tope - base.sum())
    if resto > 0:
        orden = (cuota - base).sort_values(ascending=False).index.tolist()
        for d in orden[:resto]:
            base[d] += 1

    rng = np.random.default_rng(semilla)
    partes = []
    for d, g in gdf.groupby(col_distrito):
        k = int(min(base.get(d, 0), len(g)))
        if k <= 0:
            continue
        idx = rng.choice(g.index.to_numpy(), size=k, replace=False)
        partes.append(gdf.loc[idx])

    muestra = pd.concat(partes).sort_index()
    if hasattr(gdf, "crs"):
        muestra = gpd.GeoDataFrame(muestra, geometry=gdf.geometry.name, crs=gdf.crs)
    muestra["en_muestra"] = True

    reporte = {
        "n_total": int(n), "tope": int(tope), "fraccion": round(frac, 4),
        "n_muestra": int(len(muestra)), "n_distritos": int(tam.size),
        "distritos_sin_muestra": int((base <= 0).sum()),
        "puntos_por_distrito_min": int(base[base > 0].min()) if (base > 0).any() else 0,
        "puntos_por_distrito_max": int(base.max()),
        "estrategia": "estratificado_por_distrito (proporcional, restos mayores)",
        "semilla": int(semilla),
        "implicacion_error_muestral": (
            f"se rutea el {frac:.1%} de los centros poblados; las métricas de "
            "cobertura de la Fase 3 son estimaciones con error de muestreo. Al no "
            "haber población en la fuente, la estratificación es por distrito (no "
            "ponderada por población): distritos con muchos centros poblados pequeños "
            "quedan sub-muestreados respecto a un diseño poblacional."),
    }
    return muestra, reporte


def acceso_mas_cercano(matriz_long: pd.DataFrame,
                       col_origen: str = "cod_ccpp",
                       col_destino: str = "cod_ipress") -> pd.DataFrame:
    """De la matriz larga saca, por (origen, perfil), el destino de menor
    duración. Marca routable=False si el origen no alcanza ningún destino."""
    m = matriz_long.copy()
    ruteables = m.dropna(subset=["duration_s"])
    idx = ruteables.groupby([col_origen, "profile"])["duration_s"].idxmin()
    mejor = ruteables.loc[idx, [col_origen, "profile", col_destino,
                                "duration_s", "distance_m"]].copy()
    mejor = mejor.rename(columns={col_destino: "cod_destino_cercano",
                                  "duration_s": "duration_s_min",
                                  "distance_m": "distance_m_min"})
    mejor["duration_min"] = mejor["duration_s_min"] / 60.0
    mejor["distance_km"] = mejor["distance_m_min"] / 1000.0
    mejor["routable"] = True

    todos = m[[col_origen, "profile"]].drop_duplicates()
    fusion = todos.merge(mejor[[col_origen, "profile", "routable"]],
                         on=[col_origen, "profile"], how="left")
    faltan = fusion[fusion["routable"].isna()].drop(columns="routable")
    if len(faltan):
        faltan = faltan.assign(cod_destino_cercano=pd.NA, duration_s_min=np.nan,
                               distance_m_min=np.nan, duration_min=np.nan,
                               distance_km=np.nan, routable=False)
        mejor = pd.concat([mejor, faltan], ignore_index=True)
    return mejor.reset_index(drop=True)


# ===========================================================================
# Caché Parquet de la matriz
# ===========================================================================
_CLAVES_MATRIZ = ["cod_ccpp", "cod_ipress", "profile"]
_COLS_MATRIZ = _CLAVES_MATRIZ + ["duration_s", "distance_m"]


def _cargar_cache(path: Path) -> pd.DataFrame:
    if path and path.exists():
        try:
            return pd.read_parquet(path)
        except Exception as e:  # pragma: no cover
            log.warning("caché ilegible en %s (%s); se recomputa", path, e)
    return pd.DataFrame(columns=_COLS_MATRIZ)


def _guardar_cache(df_nuevo: pd.DataFrame, path: Path,
                   claves: list[str] = _CLAVES_MATRIZ) -> pd.DataFrame:
    path.parent.mkdir(parents=True, exist_ok=True)
    previo = _cargar_cache(path) if path.exists() else pd.DataFrame(columns=df_nuevo.columns)
    combinado = (pd.concat([previo, df_nuevo], ignore_index=True)
                 .drop_duplicates(subset=claves, keep="last"))
    combinado.to_parquet(path, index=False)
    return combinado


# ===========================================================================
# Grafo vial — construcción y caché
# ===========================================================================
# Caché de proceso: el .pbf de Perú se escanea UNA sola vez (todas las vías
# `highway` con las coordenadas ya resueltas), así un `--all` no lo relee.
_CACHE_VIAS: dict = {}          # pbf_path -> lista de (coords, hw, maxspeed, oneway, junction)


def _leer_vias_pbf(pbf_path: Path) -> list:
    """Lee, en una sola pasada, todas las vías `highway` de Perú con las
    coordenadas de sus nodos ya resueltas. Usa un índice de localizaciones
    respaldado en disco (`sparse_file_array`) para no cargar en RAM las
    coordenadas de los ~30 M de nodos del país."""
    key = str(pbf_path)
    if key in _CACHE_VIAS:
        return _CACHE_VIAS[key]
    import osmium
    HW_TODOS = _HW_CAR | _HW_BIKE | _HW_FOOT
    idx_path = _ruta("data/interim") / "nodecache.bin"
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    class _V(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.vias = []

        def way(self, w):
            hw = w.tags.get("highway")
            if hw not in HW_TODOS or w.tags.get("access") in ("private", "no"):
                return
            try:
                coords = [(n.lon, n.lat) for n in w.nodes]
            except osmium.InvalidLocationError:
                return
            if len(coords) < 2:
                return
            self.vias.append((coords, hw, w.tags.get("maxspeed"),
                              w.tags.get("oneway"), w.tags.get("junction")))

    v = _V()
    v.apply_file(str(pbf_path), locations=True,
                 idx=f"sparse_file_array,{idx_path}")
    try:
        idx_path.unlink()
    except OSError:
        pass
    log.info("  pbf: %d vías highway de Perú con coordenadas (%.0fs, cacheado en memoria)",
             len(v.vias), time.time() - t0)
    _CACHE_VIAS[key] = v.vias
    return v.vias


def _ox():
    import osmnx as ox
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(_ruta("data/interim/osmnx_cache"))
    ox.settings.requests_timeout = 600
    ox.settings.log_console = False
    ox.settings.overpass_rate_limit = True
    # área máxima por sub-consulta a Overpass: 5000 km² (el default de OSMnx,
    # 2500, parte los departamentos en el doble de sub-consultas). Con la red
    # `drive` (sin caminos de herradura) el volumen por sub-consulta es bajo.
    ox.settings.max_query_area_size = 5_000_000_000
    return ox


def poligono_departamento(cfg: dict, nombre_depto: str):
    """Polígono (EPSG:4326) del departamento, desde limites_departamento.gpkg."""
    ruta = _ruta(cfg["fuentes"]["limites_administrativos"]["capas"]["departamento"]["archivo_local"])
    g = gpd.read_file(ruta)
    g = g.set_crs(4326) if g.crs is None else g.to_crs(4326)
    col = next((c for c in g.columns if c.lower() in ("nombdep", "departamento", "nombre")), None)
    if col is None:
        raise RuntimeError(f"no encuentro la columna de nombre de departamento en {ruta}")
    sel = g[g[col].astype(str).str.upper() == nombre_depto.upper()]
    if sel.empty:
        raise RuntimeError(f"'{nombre_depto}' no está en {ruta} (columna {col})")
    return sel.geometry.union_all()


def area_de_interes(gdfs: list, buffer_km: float):
    """Unión de los puntos de `gdfs` dilatados `buffer_km` (NO el casco convexo:
    en Ucayali la población sigue el río y unas pocas carreteras, así que la
    unión bufferada es una banda ramificada, mucho más chica que el casco).
    Es el área para la que se descarga la red vial."""
    pts = pd.concat([g.geometry for g in gdfs], ignore_index=True)
    gs = gpd.GeoSeries(pts, crs=4326).to_crs(3857)
    area = gs.buffer(buffer_km * 1000.0).union_all()
    # un poco de simplificación para no mandar a Overpass un polígono con
    # miles de vértices
    area = area.simplify(1000.0)
    return gpd.GeoSeries([area], crs=3857).to_crs(4326).iloc[0]


def _aplicar_velocidades(G, perfil: str, rcfg: dict):
    """Asigna 'speed_kph' y 'travel_time' (s) a cada arista según el perfil."""
    if perfil == "car":
        for _, _, data in G.edges(data=True):
            v = velocidad_car_kmh(data.get("highway"), data.get("maxspeed"))
            data["speed_kph"] = v
            data["travel_time"] = travel_time_s(data.get("length", 0.0), v)
    else:
        v = float(rcfg["velocidades_kmh"][perfil])
        for _, _, data in G.edges(data=True):
            data["speed_kph"] = v
            data["travel_time"] = travel_time_s(data.get("length", 0.0), v)
    return G


# --- tipos de vía OSM admitidos por perfil (aprox. equivalente a los filtros de OSMnx) ---
_HW_CAR = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
           "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
           "residential", "living_street", "road"}
_HW_BIKE = (_HW_CAR - {"motorway", "motorway_link"}) | {"cycleway", "track", "path", "service"}
_HW_FOOT = (_HW_CAR - {"motorway", "motorway_link"}) | {
    "footway", "path", "pedestrian", "steps", "track", "cycleway", "service", "bridleway"}
_HW_POR_PERFIL = {"car": _HW_CAR, "bike": _HW_BIKE, "foot": _HW_FOOT}


def _grafo_desde_pbf(pbf_path: Path, bbox: tuple, perfil: str, rcfg: dict):
    """Arma el grafo NetworkX del perfil a partir de la caché de vías del .pbf
    (`_leer_vias_pbf`, un solo escaneo). Se recorta al `bbox` (área combinada
    de los 3 deptos): solo entran vías con >= 2 vértices dentro.

    Los nodos del grafo se identifican por su coordenada redondeada a 7
    decimales (~1 cm): así los extremos compartidos entre vías coinciden y la
    red queda conexa."""
    import networkx as nx

    admitidos = _HW_POR_PERFIL[perfil]
    respeta_oneway = (perfil == "car")
    minx, miny, maxx, maxy = bbox
    t0 = time.time()
    vias = _leer_vias_pbf(pbf_path)

    def _nid(x, y):
        return (round(x, 7), round(y, 7))

    G = nx.MultiDiGraph()
    G.graph["crs"] = "epsg:4326"
    for coords, hw, ms, oneway_tag, junction in vias:
        if hw not in admitidos:
            continue
        if not any(minx <= x <= maxx and miny <= y <= maxy for x, y in coords):
            continue
        oneway = respeta_oneway and (oneway_tag in ("yes", "true", "1")
                                     or junction == "roundabout"
                                     or hw in ("motorway", "motorway_link"))
        seq = list(reversed(coords)) if (respeta_oneway and oneway_tag == "-1") else coords
        for (ax, ay), (bx, by) in zip(seq, seq[1:]):
            a, b = _nid(ax, ay), _nid(bx, by)
            if a == b:
                continue
            L = haversine_m(ax, ay, bx, by)
            G.add_edge(a, b, length=float(L), highway=hw, maxspeed=ms)
            if not oneway:
                G.add_edge(b, a, length=float(L), highway=hw, maxspeed=ms)
    for (x, y) in list(G.nodes):
        G.nodes[(x, y)]["x"] = float(x)
        G.nodes[(x, y)]["y"] = float(y)

    if G.number_of_nodes():   # componente débilmente conexa mayor (≈ retain_all=False)
        G = G.subgraph(max(nx.weakly_connected_components(G), key=len)).copy()
    n0, e0 = G.number_of_nodes(), G.number_of_edges()
    _simplificar_grafo(G)     # colapsa los nodos-forma (grado 2) -> ~5-10x menos nodos
    _aplicar_velocidades(G, perfil, rcfg)
    log.info("  grafo %s: %d->%d nodos, %d->%d aristas (%.0fs)",
             perfil, n0, G.number_of_nodes(), e0, G.number_of_edges(), time.time() - t0)
    return G


def _simplificar_grafo(G) -> None:
    """Colapsa in-place las cadenas de nodos de grado 2 (puntos-forma de una
    vía, no cruces): u—v—w con `v` intersticial se vuelve u—w con la longitud
    sumada. Reduce el grafo ~5-10x y acelera Dijkstra. Repite hasta punto fijo."""
    cambiado = True
    while cambiado:
        cambiado = False
        for v in list(G.nodes):
            if v not in G:
                continue
            vecinos = (set(G.predecessors(v)) | set(G.successors(v)))
            vecinos.discard(v)
            if len(vecinos) != 2:
                continue
            u, w = tuple(vecinos)

            def _e(frm, to):
                dd = G.get_edge_data(frm, to)
                return next(iter(dd.values())) if dd else None

            uv, vw, wv, vu = _e(u, v), _e(v, w), _e(w, v), _e(v, u)
            # aristas incidentes a v que NO van a u/w -> no es un simple pasa-por
            inc = set(G.predecessors(v)) | set(G.successors(v))
            if inc - {u, w}:
                continue
            nuevas = []
            if uv and vw:
                nuevas.append((u, w, uv["length"] + vw["length"], uv.get("highway"), uv.get("maxspeed")))
            if wv and vu:
                nuevas.append((w, u, wv["length"] + vu["length"], wv.get("highway"), wv.get("maxspeed")))
            if not nuevas:
                continue
            G.remove_node(v)
            for frm, to, L, hw, ms in nuevas:
                G.add_edge(frm, to, length=float(L), highway=hw, maxspeed=ms)
            cambiado = True


def construir_o_cargar_grafo(cfg: dict, nombre_depto: str, perfil: str,
                             forzar: bool, area=None, bbox: tuple | None = None):
    """Devuelve el grafo NetworkX del (departamento, perfil). Cachea en
    data/interim/graphs/<depto>_<perfil>.pkl.

    `bbox` (minx,miny,maxx,maxy en EPSG:4326) acota qué red se lee; si es None
    se usa `area.bounds` y si tampoco hay `area`, el polígono del departamento.
    Pasar el MISMO bbox (área combinada de los 3 deptos) en todas las llamadas
    hace que el .pbf se escanee una sola vez para las coordenadas.

    Fuente por defecto: el `.pbf` local de la Fase 1 (pyosmium; sin red).
    Fallback (config `grafo.fuente: osmnx_overpass`): descarga por Overpass.
    """
    rcfg = cfg["routing"]
    cache_dir = _ruta(rcfg["grafo"]["cache_dir"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    pkl = cache_dir / f"{nombre_depto.lower()}_{perfil}.pkl"

    if pkl.exists() and not forzar:
        log.info("  grafo %s/%s desde caché %s", nombre_depto, perfil, pkl.name)
        with open(pkl, "rb") as f:
            return pickle.load(f)

    if bbox is None:
        area = area if area is not None else poligono_departamento(cfg, nombre_depto)
        bbox = tuple(area.bounds)
    fuente = rcfg["grafo"].get("fuente", "pbf_local")

    if fuente == "pbf_local":
        pbf = _ruta(cfg["fuentes"]["osm_peru"]["archivo_local"])
        if not pbf.exists():
            raise FileNotFoundError(f"no existe {pbf}. Corre la Fase 1 (o cambia grafo.fuente).")
        log.info("  construyendo grafo %s/%s desde %s ...", nombre_depto, perfil, pbf.name)
        G = _grafo_desde_pbf(pbf, bbox, perfil, rcfg)
    else:  # osmnx_overpass
        from shapely.geometry import box as _box
        ox = _ox()
        log.info("  descargando red OSM %s/%s por Overpass ...", nombre_depto, perfil)
        poly = area if area is not None else _box(*bbox)
        G = ox.graph_from_polygon(poly, network_type=_NETWORK_TYPE[perfil],
                                  simplify=True, retain_all=False)
        _aplicar_velocidades(G, perfil, rcfg)

    with open(pkl, "wb") as f:
        pickle.dump(G, f)
    return G


# ===========================================================================
# Snapping
# ===========================================================================
def snap_nodos(G, df: pd.DataFrame, id_col: str, perfil: str,
               umbral_m: float, lon_col: str = "lon", lat_col: str = "lat"
               ) -> tuple[pd.DataFrame, dict]:
    """Engancha cada punto al nodo más cercano del grafo (KDTree en un plano
    local métrico). Marca snap_ok=False si el nodo queda a más de `umbral_m`.
    Devuelve (snap_df, {id_punto: nodo_del_grafo}). Los nodos son tuplas
    (lon,lat) redondeadas, así que van al DataFrame como nodo_lon/nodo_lat.
    """
    from scipy.spatial import cKDTree

    lons = df[lon_col].to_numpy(dtype=float)
    lats = df[lat_col].to_numpy(dtype=float)
    claves = list(G.nodes)
    nx_ = np.array([G.nodes[k]["x"] for k in claves], dtype=float)
    ny_ = np.array([G.nodes[k]["y"] for k in claves], dtype=float)

    lat0 = float(np.median(ny_)) if len(ny_) else 0.0
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 110_540.0
    arbol = cKDTree(np.column_stack([nx_ * kx, ny_ * ky]))
    _, idx = arbol.query(np.column_stack([lons * kx, lats * ky]), k=1)
    idx = np.atleast_1d(idx)

    filas, mapa = [], {}
    for i, j in enumerate(idx):
        nodo = claves[int(j)]
        pid = df[id_col].iloc[i]
        dist = haversine_m(lons[i], lats[i], G.nodes[nodo]["x"], G.nodes[nodo]["y"])
        mapa[pid] = nodo
        filas.append({"id": pid, "profile": perfil, "lon": lons[i], "lat": lats[i],
                      "nodo_lon": G.nodes[nodo]["x"], "nodo_lat": G.nodes[nodo]["y"],
                      "snap_dist_m": dist, "snap_ok": bool(dist <= umbral_m)})
    return pd.DataFrame(filas), mapa


def resumen_snapping(snap_df: pd.DataFrame) -> pd.DataFrame:
    g = snap_df.groupby(["profile", "tipo"]) if "tipo" in snap_df.columns else snap_df.groupby(["profile"])
    return pd.DataFrame({
        "n_puntos": g.size(),
        "n_snap_fallido": g["snap_ok"].apply(lambda s: int((~s).sum())),
        "snap_dist_media_m": g["snap_dist_m"].mean().round(1),
        "snap_dist_p95_m": g["snap_dist_m"].quantile(0.95).round(1),
        "snap_dist_max_m": g["snap_dist_m"].max().round(1),
    }).reset_index()


# ===========================================================================
# Ruteo sobre el grafo
# ===========================================================================
def tiempos_desde_instalacion(G, GR, nodo_inst) -> tuple[dict, dict]:
    """(dur_s, dist_m) desde CUALQUIER nodo hasta `nodo_inst`, calculado como
    Dijkstra desde `nodo_inst` sobre el grafo invertido GR."""
    import networkx as nx
    dur = nx.single_source_dijkstra_path_length(GR, nodo_inst, weight="travel_time")
    dist = nx.single_source_dijkstra_path_length(GR, nodo_inst, weight="length")
    return dur, dist


def matriz_departamento(cfg: dict, nombre_depto: str, perfil: str,
                        muestra_dep: "gpd.GeoDataFrame", oferta_dep: "gpd.GeoDataFrame",
                        forzar: bool, bbox: tuple | None = None
                        ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve (matriz_long, snap_df) para un departamento y perfil.
    - perfil en `matriz_completa_perfiles`: todos los pares origen x (resolutivos+I-3+I-4)
    - otros perfiles: solo el resolutivo más cercano por origen (multi-source Dijkstra)
    `bbox` (área combinada de los 3 deptos): si se pasa, el .pbf se lee 1 sola vez.
    """
    import networkx as nx
    rcfg = cfg["routing"]
    umbral = float(rcfg.get("snap_umbral_m", 1500))
    cats_matriz = set(rcfg["categorias_matriz"])
    completa = perfil in rcfg.get("matriz_completa_perfiles", ["car"])
    buffer_km = float(rcfg["grafo"].get("buffer_km", 30))

    of = oferta_dep.copy()
    of["cod_ipress"] = of["COD_IPRESS"].to_numpy()
    destinos = of[of["categoria_norm"].isin(cats_matriz) | of["resolutiva"].astype(bool)].copy()
    resol = of[of["resolutiva"].astype(bool)].copy()

    if bbox is None:
        bbox = tuple(area_de_interes([muestra_dep, destinos], buffer_km).bounds)
    G = construir_o_cargar_grafo(cfg, nombre_depto, perfil, forzar, bbox=bbox)
    GR = G.reverse(copy=False)

    dem = pd.DataFrame({"cod_ccpp": muestra_dep["CODCP"].to_numpy(),
                        "lon": muestra_dep.geometry.x.to_numpy(),
                        "lat": muestra_dep.geometry.y.to_numpy()})
    dst = pd.DataFrame({"cod_ipress": destinos["cod_ipress"].to_numpy(),
                        "lon": destinos.geometry.x.to_numpy(),
                        "lat": destinos.geometry.y.to_numpy(),
                        "resolutiva": destinos["resolutiva"].astype(bool).to_numpy()})

    snap_dem, nodo_dem = snap_nodos(G, dem, "cod_ccpp", perfil, umbral)
    snap_dst, nodo_dst = snap_nodos(G, dst, "cod_ipress", perfil, umbral)
    snap_dem["tipo"] = "demanda"
    snap_dst["tipo"] = "oferta"

    filas = []
    t0 = time.time()
    if completa:
        for j, row in enumerate(dst.itertuples(index=False), 1):
            dur, dist = tiempos_desde_instalacion(G, GR, nodo_dst[row.cod_ipress])
            for cod, nd in nodo_dem.items():
                filas.append((cod, row.cod_ipress, perfil,
                              dur.get(nd, np.nan), dist.get(nd, np.nan)))
            if j % 25 == 0 or j == len(dst):
                log.info("    %s/%s matriz completa %d/%d instalaciones (%.0fs)",
                         nombre_depto, perfil, j, len(dst), time.time() - t0)
    else:
        # solo el resolutivo más cercano: Dijkstra multi-fuente desde los nodos resolutivos
        r_nodes = {nodo_dst[c] for c in resol["COD_IPRESS"].to_numpy() if c in nodo_dst}
        dur = nx.multi_source_dijkstra_path_length(GR, r_nodes, weight="travel_time")
        dist = nx.multi_source_dijkstra_path_length(GR, r_nodes, weight="length")
        # ¿a qué resolutivo? el más cercano nodo a nodo (aprox): recomputo por instalación
        # (son pocos resolutivos) para poder reportar cuál.
        por_inst = {}
        for c in resol["COD_IPRESS"].to_numpy():
            if c in nodo_dst:
                por_inst[c] = nx.single_source_dijkstra_path_length(GR, nodo_dst[c], weight="travel_time")
        for cod, nd in nodo_dem.items():
            mejor_c, mejor_t = pd.NA, np.inf
            for c, dmap in por_inst.items():
                t = dmap.get(nd, np.inf)
                if t < mejor_t:
                    mejor_t, mejor_c = t, c
            filas.append((cod, mejor_c if np.isfinite(mejor_t) else pd.NA, perfil,
                          dur.get(nd, np.nan), dist.get(nd, np.nan)))
        log.info("    %s/%s nearest-resolutivo (%d resolutivos, %.0fs)",
                 nombre_depto, perfil, len(por_inst), time.time() - t0)

    matriz = pd.DataFrame(filas, columns=_COLS_MATRIZ)
    snap_df = pd.concat([snap_dem, snap_dst], ignore_index=True)
    snap_df["departamento"] = nombre_depto
    return matriz, snap_df


# ===========================================================================
# Orquestación
# ===========================================================================
def _cargar_procesados(cfg: dict, nombres: list[str]):
    proc = _ruta(cfg["rutas"]["processed"])
    ofertas, demandas = {}, {}
    for dep in nombres:
        for tabla, dic in (("renipress", ofertas), ("centros_poblados", demandas)):
            f = proc / f"{tabla}_{dep}.gpkg"
            if not f.exists():
                raise FileNotFoundError(f"falta {f}. Corre antes la Fase 1.")
            g = gpd.read_file(f)
            g = g.set_crs(4326) if g.crs is None else g.to_crs(4326)
            if tabla == "renipress":
                g["resolutiva"] = g["resolutiva"].astype("boolean").fillna(False).astype(bool)
            dic[dep] = g
    return ofertas, demandas


def run(cfg: dict, departamentos: list[str] | None = None,
        perfiles: list[str] | None = None, forzar: bool = False) -> dict:
    if gpd is None:
        raise RuntimeError("geopandas no está instalado; run() lo necesita.")

    rcfg = cfg["routing"]
    perfiles = perfiles or rcfg["perfiles"]
    rutas = rcfg["rutas"]
    d = cfg["departamentos"]
    nombres = departamentos or [d["costero"].lower(), d["andino"].lower(), d["amazonico"].lower()]

    configurar_logging(_ruta(rutas["log_ejecucion"]))
    t_ini = time.time()
    log.info("=== Fase 2 — ruteo pyosmium/NetworkX | perfiles=%s | deptos=%s ===", perfiles, nombres)

    ofertas, demandas = _cargar_procesados(cfg, nombres)

    tope = int(rcfg["muestreo"]["tope_puntos_demanda"])
    semilla = int(rcfg["muestreo"]["semilla"])
    demanda_all = pd.concat(demandas.values(), ignore_index=True)
    demanda_all = gpd.GeoDataFrame(demanda_all, geometry="geometry", crs=4326)
    muestra, rep_muestreo = muestrear_demanda(demanda_all, tope, semilla=semilla, col_distrito="UBIGEO")
    for k, v in rep_muestreo.items():
        log.info("  muestreo · %s: %s", k, v)
    ruta_muestra = _ruta(rutas["demanda_muestreada"])
    ruta_muestra.parent.mkdir(parents=True, exist_ok=True)
    muestra.to_file(ruta_muestra, driver="GPKG")

    # bbox combinado de los 3 deptos (demanda muestreada + toda la oferta):
    # el .pbf se escanea así una sola vez para las coordenadas.
    buffer_km = float(rcfg["grafo"].get("buffer_km", 30))
    bbox_ambito = tuple(area_de_interes(
        [muestra] + list(ofertas.values()), buffer_km).bounds)
    log.info("  área de interés (bbox EPSG:4326): %s", tuple(round(c, 3) for c in bbox_ambito))

    cache_matriz = _ruta(rutas["cache_matriz"])
    matrices, snaps = [], []
    for dep in nombres:
        dep_up = dep.upper()
        m_dep = muestra[muestra["UBIGEO"].astype(str).str.zfill(6).str.startswith(
            cfg["departamentos"]["codigos_inei"][dep_up])].copy()
        of_dep = ofertas[dep]
        log.info("--- %s: %d centros poblados muestreados, %d establecimientos (%d resolutivos) ---",
                 dep_up, len(m_dep), len(of_dep), int(of_dep["resolutiva"].sum()))
        for perfil in perfiles:
            try:
                mtx, snp = matriz_departamento(cfg, dep, perfil, m_dep, of_dep, forzar,
                                               bbox=bbox_ambito)
                matrices.append(mtx)
                snaps.append(snp)
                # se cachean TODOS los pares, incl. los sin ruta (duration/distance
                # NaN): así acceso_mas_cercano() puede marcar routable=False en vez
                # de que el origen desaparezca de la matriz.
                _guardar_cache(mtx, cache_matriz)
            except Exception as e:
                log.error("  %s/%s FALLÓ: %s", dep, perfil, e)

    if not matrices:
        raise RuntimeError("no se calculó ninguna matriz (¿sin internet para Overpass?).")

    snap_df = pd.concat(snaps, ignore_index=True)
    snap_df.to_parquet(_ruta(rutas["cache_snap"]), index=False)
    resumen = resumen_snapping(snap_df)
    resumen.to_csv(_ruta(rutas["reporte_snapping"]), index=False, encoding="utf-8-sig")
    log.info("snapping:\n%s", resumen.to_string(index=False))

    matriz_full = _cargar_cache(cache_matriz)
    acceso = acceso_mas_cercano(matriz_full)
    acceso.to_parquet(_ruta(rutas["cache_acceso"]), index=False)

    no_rut = acceso[~acceso["routable"]]
    log.info("acceso: %d filas · no ruteables: %d (%.1f%%) — marcados, NO se usa recta",
             len(acceso), len(no_rut), 100 * len(no_rut) / max(len(acceso), 1))
    for perfil in sorted(acceso["profile"].unique()):
        sub = acceso[(acceso.profile == perfil) & acceso.routable]
        if len(sub):
            log.info("  %-4s  mediana=%.1f min  p90=%.1f min  máx=%.1f min",
                     perfil, sub.duration_min.median(), sub.duration_min.quantile(0.9),
                     sub.duration_min.max())

    comparacion = {}
    if {"car", "foot"}.issubset(set(acceso["profile"])):
        piv = acceso.pivot_table(index="cod_ccpp", columns="profile",
                                 values=["cod_destino_cercano", "duration_min"], aggfunc="first")
        dc, df_ = piv[("cod_destino_cercano", "car")], piv[("cod_destino_cercano", "foot")]
        amb = dc.notna() & df_.notna()
        distinto = float((dc[amb] != df_[amb]).mean()) if amb.any() else float("nan")
        ratio = float((piv[("duration_min", "foot")] / piv[("duration_min", "car")]
                       ).replace([np.inf, -np.inf], np.nan).median())
        comparacion = {"pct_instalacion_distinta_car_vs_foot": round(100 * distinto, 1),
                       "ratio_mediano_tiempo_foot_sobre_car": round(ratio, 1)}
        log.info("car vs foot: instalación más cercana difiere en %.1f%%; tiempo a pie ~%.1fx el de auto",
                 comparacion["pct_instalacion_distinta_car_vs_foot"],
                 comparacion["ratio_mediano_tiempo_foot_sobre_car"])

    resumen_run = {
        "segundos": round(time.time() - t_ini, 1),
        "perfiles": perfiles, "departamentos": nombres,
        "n_demanda_muestra": int(len(muestra)),
        "n_pares_matriz": int(len(matriz_full)),
        "n_no_ruteables": int(len(no_rut)),
        "comparacion_modos": comparacion,
        "muestreo": rep_muestreo,
        "salidas": {k: str(_ruta(v)) for k, v in rutas.items()},
    }
    log.info("=== fin en %.0fs ===", resumen_run["segundos"])
    return resumen_run


# ===========================================================================
# CLI
# ===========================================================================
def _smoke(cfg: dict, perfiles: list[str]) -> int:
    """Prueba de extremo a extremo mínima: sobre el departamento más chico,
    arma el grafo (área de interés bufferada) y rutea unos pocos centros
    poblados a su resolutivo más cercano."""
    configurar_logging(None)
    if gpd is None:
        log.error("geopandas no está instalado"); return 1
    dep = cfg["departamentos"]["amazonico"].lower()
    perfil = perfiles[0]
    ofertas, demandas = _cargar_procesados(cfg, [dep])
    dem = demandas[dep]
    dem = dem.sample(n=min(30, len(dem)), random_state=0)
    log.info("smoke: %s/%s · %d centros poblados de prueba", dep, perfil, len(dem))
    mtx, snp = matriz_departamento(cfg, dep, perfil, dem, ofertas[dep], forzar=False)
    acc = acceso_mas_cercano(mtx)
    ok = acc[acc["routable"]]
    log.info("snapping falla: %d/%d", int((~snp["snap_ok"]).sum()), len(snp))
    log.info("ruteables: %d/%d · tiempo mediano al resolutivo más cercano: %.1f min",
             len(ok), len(acc), ok["duration_min"].median() if len(ok) else float("nan"))
    return 0 if len(ok) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fase 2 — ruteo OSMnx/NetworkX")
    ap.add_argument("--departamento", help="piura | ayacucho | ucayali (default: los 3)")
    ap.add_argument("--all", action="store_true", help="los 3 departamentos de config.md")
    ap.add_argument("--perfil", default=None, help="subconjunto, p.ej. car,foot")
    ap.add_argument("--forzar", action="store_true", help="ignora la caché (grafos y matriz)")
    ap.add_argument("--smoke", action="store_true", help="arma 1 grafo y rutea 1 par")
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)

    cfg = cargar_config(a.config)
    perfiles = a.perfil.split(",") if a.perfil else cfg["routing"]["perfiles"]

    if a.smoke:
        return _smoke(cfg, perfiles)

    deptos = None
    if a.departamento:
        deptos = [a.departamento.lower()]
    elif not a.all:
        log.warning("usa --all o --departamento <nombre> (sugerido: --departamento ucayali)")
        return 2
    run(cfg, departamentos=deptos, perfiles=perfiles, forzar=a.forzar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
