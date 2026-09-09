# -*- coding: utf-8 -*-
"""
Fase 3 (insumo) — Población por centro poblado.

La fuente de demanda (SIGMED/MINEDU) **no trae población**. Para poder
ponderar las métricas de la Fase 3 se estima la población de cada centro
poblado a partir del raster **WorldPop** (Global 2000-2020, *constrained*,
2020, ~1 km, personas por celda).

Método (conserva el total, sin doble conteo):
  cada celda poblada del raster se asigna al **centro poblado muestreado más
  cercano** (asignación tipo Voronoi) y se suman. Así los 5 000 puntos
  muestreados quedan representando la población de su "área de captación" y
  la suma reproduce la población total de la región — la Fase 3 estima
  cobertura sobre población total, y el error de muestreo queda en *dónde*
  caen los límites de captación, no en población faltante.

Fuente a citar en el informe:
  WorldPop (www.worldpop.org). Global High Resolution Population Denominators
  Project. Perú, 2020, UN-adjusted, constrained. DOI:10.5258/SOTON/WP00685.

    python src/poblacion.py                 # descarga (si falta) y calcula
    python src/poblacion.py --forzar        # recalcula
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]

# WorldPop 1 km, UN-ajustado, *no* constrained (distribuye la población del
# censo por TODO el territorio, también el rural disperso — necesario para
# ponderar centros poblados pequeños). ~7 MB.
WORLDPOP_URLS = [
    "https://data.worldpop.org/GIS/Population/Global_2000_2020_1km_UNadj/2020/PER/per_ppp_2020_1km_Aggregated_UNadj.tif",
    "https://data.worldpop.org/GIS/Population/Global_2000_2020_1km/2020/PER/per_ppp_2020_1km_Aggregated.tif",
]


def cargar_config(path=None) -> dict:
    import yaml
    path = Path(path) if path else REPO_ROOT / "config.md"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ruta(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else REPO_ROOT / p


def descargar_worldpop(destino: Path) -> Path:
    import requests
    if destino.exists() and destino.stat().st_size > 1_000_000:
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    for url in WORLDPOP_URLS:
        try:
            print(f"descargando WorldPop: {url}")
            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                tmp = destino.with_suffix(".part")
                with open(tmp, "wb") as f:
                    for ch in r.iter_content(1024 * 1024):
                        f.write(ch)
                tmp.replace(destino)
            print(f"  -> {destino} ({destino.stat().st_size/1e6:.1f} MB)")
            return destino
        except Exception as e:  # pragma: no cover
            print(f"  falló ({e}); pruebo siguiente URL")
    raise RuntimeError("no se pudo descargar el raster de WorldPop; "
                       "descárgalo manualmente a " + str(destino))


def _poblacion_grupo(src, ccpp_xy: np.ndarray, codigos: np.ndarray, poligono) -> np.ndarray:
    """Para un grupo de centros poblados (ccpp_xy: Nx2 lon/lat) de un mismo
    departamento, suma la población de cada celda del raster **dentro del
    polígono `poligono` del departamento**, asignándola al centro poblado más
    cercano (Voronoi). Así el total del grupo = población WorldPop del
    departamento (coincide con el censo) y no absorbe población vecina."""
    import rasterio
    import shapely

    xs, ys = ccpp_xy[:, 0], ccpp_xy[:, 1]
    minx, miny, maxx, maxy = poligono.bounds
    win = src.window(minx, miny, maxx, maxy)
    arr = src.read(1, window=win, boundless=True, fill_value=0).astype("float32")
    transform = src.window_transform(win)

    if src.nodata is not None:
        arr[arr == src.nodata] = 0.0
    arr[~np.isfinite(arr) | (arr < 0)] = 0.0

    filas, cols = np.nonzero(arr > 0)
    pob = np.zeros(len(codigos), dtype="float64")
    if len(filas) == 0:
        return pob
    cx, cy = rasterio.transform.xy(transform, filas, cols, offset="center")
    cx, cy = np.asarray(cx), np.asarray(cy)
    val = arr[filas, cols]

    dentro = shapely.contains_xy(poligono, cx, cy)     # recorta al departamento
    cx, cy, val = cx[dentro], cy[dentro], val[dentro]
    if len(val) == 0:
        return pob

    from scipy.spatial import cKDTree
    lat0 = float(np.median(ys))
    kx = 111_320.0 * np.cos(np.radians(lat0))
    ky = 110_540.0
    arbol = cKDTree(np.column_stack([xs * kx, ys * ky]))
    _, idx = arbol.query(np.column_stack([cx * kx, cy * ky]), k=1)
    np.add.at(pob, idx, val)
    return pob


def poblacion_por_centro_poblado(raster_path: Path, ccpp, limites_dep) -> pd.DataFrame:
    """`ccpp`: GeoDataFrame de puntos (EPSG:4326) con 'cod_ccpp' y 'UBIGEO'.
    `limites_dep`: GeoDataFrame de polígonos departamentales con columna 'ccdd'
    (código INEI de 2 dígitos). Procesa por departamento. Devuelve
    DataFrame [cod_ccpp, poblacion]."""
    import rasterio

    codigos = ccpp["cod_ccpp"].astype(str).to_numpy()
    xy = np.column_stack([ccpp.geometry.x.to_numpy(), ccpp.geometry.y.to_numpy()])
    grupo = ccpp["UBIGEO"].astype(str).str.zfill(6).str[:2].to_numpy()
    lim = limites_dep.copy()
    lim["ccdd"] = lim["ccdd"].astype(str).str.zfill(2)

    pob = np.zeros(len(codigos), dtype="float64")
    with rasterio.open(raster_path) as src:
        for g in np.unique(grupo):
            m = grupo == g
            poly = lim.loc[lim["ccdd"] == g, "geometry"]
            if poly.empty:
                print(f"  depto {g}: sin polígono en límites; se omite el recorte")
                continue
            poly = poly.union_all()
            pob[m] = _poblacion_grupo(src, xy[m], codigos[m], poly)
            print(f"  depto {g}: {m.sum()} centros poblados · población asignada {pob[m].sum():,.0f}")
    return pd.DataFrame({"cod_ccpp": codigos, "poblacion": np.round(pob, 1)})


def run(cfg: dict, forzar: bool = False) -> pd.DataFrame:
    import geopandas as gpd
    mcfg = cfg.get("metrics", {})
    raster = _ruta(mcfg.get("raster_poblacion", "data/raw/peru_worldpop_2020_1km.tif"))
    salida = _ruta(mcfg.get("archivo_poblacion",
                            "data/processed/centros_poblados_poblacion.parquet"))
    if salida.exists() and not forzar:
        print(f"ya existe {salida.relative_to(REPO_ROOT)} (usa --forzar para recalcular)")
        return pd.read_parquet(salida)

    descargar_worldpop(raster)
    dem = gpd.read_file(_ruta(cfg["routing"]["rutas"]["demanda_muestreada"]))
    dem = dem.set_crs(4326) if dem.crs is None else dem.to_crs(4326)
    dem["cod_ccpp"] = dem["CODCP"].astype(str)

    ruta_lim = _ruta(cfg["fuentes"]["limites_administrativos"]["capas"]["departamento"]["archivo_local"])
    limites = gpd.read_file(ruta_lim)
    limites = limites.set_crs(4326) if limites.crs is None else limites.to_crs(4326)

    pob = poblacion_por_centro_poblado(raster, dem, limites)
    salida.parent.mkdir(parents=True, exist_ok=True)
    pob.to_parquet(salida, index=False)
    print(f"\npoblación estimada -> {salida.relative_to(REPO_ROOT)}")
    print(f"  centros poblados: {len(pob)}  ·  población total asignada: {pob['poblacion'].sum():,.0f}")
    print(f"  mediana: {pob['poblacion'].median():.0f}  ·  máx: {pob['poblacion'].max():,.0f}"
          f"  ·  con 0 hab.: {(pob['poblacion'] == 0).sum()}")
    return pob


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fase 3 — población por centro poblado (WorldPop)")
    ap.add_argument("--forzar", action="store_true")
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    try:
        run(cargar_config(a.config), forzar=a.forzar)
    except FileNotFoundError as e:
        print(f"ERROR: falta {e}. Corre antes la Fase 2 (genera demanda_muestreada.gpkg).",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
