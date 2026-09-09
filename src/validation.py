# -*- coding: utf-8 -*-
"""
Fase 1 (b) — Limpieza, validación espacial, filtrado de ámbito y almacenamiento.

Pasos:
  2. Limpieza y validación espacial
     - estandarización de texto: categorías (`II-1`) y estados (`ACTIVO`),
       reversión de mojibake UTF-8/latin-1;
     - coordenadas: vacías/no numéricas, en cero, **signo de hemisferio
       invertido** (corregido), **intercambio lat/lon** (corregido), fuera del
       bounding box de Perú (eliminado);
     - códigos duplicados; punto fuera de su polígono distrital (marcado);
     - todo se cuantifica en `logs/quality_report.csv` (+ `.md`).
  3. Filtrado de ámbito: los 3 departamentos de `config.md`.
  4. Almacenamiento: GeoPackage en `data/processed/` (capa nacional + un
     recorte por departamento).
  5. Mapas Folium por departamento -> `data/outputs/mapa_acceso_<depto>.html`.

    python src/validation.py             # corre todo
    python src/validation.py --sin-mapas # omite el Paso 5

Cada métrica/regla es una función testeable (ver tests/test_validation.py).
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# permite tanto `python src/validation.py` como `python -m src.validation`
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.acquisition import (REPO_ROOT, cargar_config, codigos_inei,  # noqa: E402
                             departamentos_objetivo, leer_csv_con_encoding, ruta)


# ===========================================================================
# Informe de calidad de datos
# ===========================================================================
class QualityReport:
    def __init__(self):
        self.filas: list[dict] = []

    def check(self, dataset, regla, evaluados, marcados, accion, motivo):
        pct = round(100 * marcados / evaluados, 2) if evaluados else None
        self.filas.append({"dataset": dataset, "regla": regla,
                           "registros_evaluados": int(evaluados),
                           "registros_marcados": int(marcados), "porcentaje": pct,
                           "accion": accion, "motivo": motivo})
        pct_txt = f" ({pct}%)" if pct is not None else ""
        print(f"  [{dataset}] {regla}: {marcados}/{evaluados}{pct_txt} -> {accion}")

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.filas)


# ===========================================================================
# Estandarización de texto (funciones puras)
# ===========================================================================
_PAT_CAT = re.compile(r"^(I{1,3})-?(1|2|3|4|E)$")   # I-4 existe (categoría I-4 de RENIPRESS)
_CAT_VACIA = {"", "0", "NAN", "NONE", "S/C", "SINCATEGORIA", "SINCATEGORÍA"}
_MOJIBAKE = {"ÿ": "ñ", "Ÿ": "Ñ"}
_DOBLE_COD = ("Ã", "Â", "�")


def normalizar_categoria(raw) -> str | None:
    """`' ii 1 '` -> `'II-1'`; vacío / '0' / 'S/C' / no reconocible -> None
    (se conserva el dato crudo pero no cuenta como oferta resolutiva)."""
    if raw is None:
        return None
    txt = re.sub(r"\s+", "", str(raw).strip().upper())
    if txt in _CAT_VACIA:
        return None
    m = _PAT_CAT.match(txt)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def grupo_categoria(cat_norm, resolutivas: set, no_concluyentes: set) -> str:
    if cat_norm in resolutivas:
        return "resolutiva"
    if cat_norm in no_concluyentes:
        return "no_concluyente"
    return "desconocida"


def normalizar_estado(raw) -> str:
    return str(raw).strip().upper()


def arreglar_mojibake(v):
    """Devuelve (valor_corregido, cambió). Revierte el mojibake conocido de
    RENIPRESS (ÿ->ñ) y la doble codificación utf-8 leída como latin-1."""
    if not isinstance(v, str) or not v:
        return v, False
    out, cambio = v, False
    for malo, bueno in _MOJIBAKE.items():
        if malo in out:
            out = out.replace(malo, bueno)
            cambio = True
    if any(x in out for x in _DOBLE_COD):
        try:
            out = out.encode("latin-1").decode("utf-8")
            cambio = True
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
    return out, cambio


def arreglar_encoding_columnas(df, columnas, dataset, qr: QualityReport):
    n = 0
    for c in columnas:
        if c not in df.columns:
            continue
        res = df[c].map(arreglar_mojibake)
        df[c] = res.map(lambda r: r[0])
        n += int(sum(r[1] for r in res))
    qr.check(dataset, "encoding_texto_utf8_vs_latin1", len(df), n,
             "corregido" if n else "sin problemas",
             "mojibake conocido (ÿ→ñ) y doble codificación utf-8/latin-1")
    return df


# ===========================================================================
# Validación de coordenadas (funciones puras)
# ===========================================================================
def validar_coordenadas_xy(df, lat_col, lon_col, bbox: dict, tol: float,
                           dataset: str, qr: QualityReport):
    """Limpia lat/lon numéricas y añade columnas `_lat`/`_lon`. Reglas en
    orden: faltantes -> elimina; ≈0 -> elimina; signo de hemisferio -> corrige;
    intercambio lat/lon -> corrige; fuera del bbox -> elimina."""
    n0 = len(df)
    lat = pd.to_numeric(df[lat_col], errors="coerce")
    lon = pd.to_numeric(df[lon_col], errors="coerce")

    falta = lat.isna() | lon.isna()
    qr.check(dataset, "coordenadas_vacias_o_no_numericas", n0, int(falta.sum()),
             "eliminado", "sin coordenada numérica no se puede rutear en la Fase 2")
    df, lat, lon = df.loc[~falta].copy(), lat.loc[~falta], lon.loc[~falta]

    cero = lat.abs().lt(tol) | lon.abs().lt(tol)
    qr.check(dataset, "coordenadas_en_cero", n0, int(cero.sum()),
             "eliminado", f"|valor| < {tol} se trata como nulo, no como posición cerca de (0,0)")
    df, lat, lon = df.loc[~cero].copy(), lat.loc[~cero], lon.loc[~cero]

    lat_pos = lat.gt(0) & (-lat).between(bbox["lat_min"], bbox["lat_max"])
    lon_pos = lon.gt(0) & (-lon).between(bbox["lon_min"], bbox["lon_max"])
    n_signo = int((lat_pos | lon_pos).sum())
    lat = lat.mask(lat_pos, -lat)
    lon = lon.mask(lon_pos, -lon)
    qr.check(dataset, "signo_de_hemisferio_invertido", n0, n_signo,
             "corregido", "coordenada positiva cuyo valor negativo sí cae dentro de Perú")

    dentro = lat.between(bbox["lat_min"], bbox["lat_max"]) & lon.between(bbox["lon_min"], bbox["lon_max"])
    dentro_swap = lon.between(bbox["lat_min"], bbox["lat_max"]) & lat.between(bbox["lon_min"], bbox["lon_max"])
    swap = (~dentro) & dentro_swap
    qr.check(dataset, "coordenadas_lat_lon_intercambiadas", n0, int(swap.sum()),
             "corregido", "(lat,lon) cae fuera de Perú pero (lon,lat) cae dentro: se intercambian")
    lat_f = lat.mask(swap, lon)
    lon_f = lon.mask(swap, lat)

    fuera = ~(lat_f.between(bbox["lat_min"], bbox["lat_max"])
              & lon_f.between(bbox["lon_min"], bbox["lon_max"]))
    qr.check(dataset, "coordenadas_fuera_de_peru", n0, int(fuera.sum()), "eliminado",
             f"fuera de lon[{bbox['lon_min']},{bbox['lon_max']}] x lat[{bbox['lat_min']},{bbox['lat_max']}]")
    df = df.loc[~fuera].copy()
    df["_lat"] = lat_f.loc[df.index]
    df["_lon"] = lon_f.loc[df.index]
    return df


def validar_geometria_puntos(gdf, bbox: dict, dataset: str, qr: QualityReport):
    n0 = len(gdf)
    mala = gdf.geometry.isna() | gdf.geometry.is_empty
    qr.check(dataset, "geometria_vacia", n0, int(mala.sum()), "eliminado",
             "sin geometría no se puede rutear")
    gdf = gdf.loc[~mala].copy()
    p = gdf.geometry.representative_point()
    dentro = p.y.between(bbox["lat_min"], bbox["lat_max"]) & p.x.between(bbox["lon_min"], bbox["lon_max"])
    qr.check(dataset, "coordenadas_fuera_de_peru", n0, int((~dentro).sum()),
             "eliminado", "el punto representativo cae fuera del bbox de Perú")
    return gdf.loc[dentro].copy()


def marcar_duplicados(df, id_col, dataset: str, qr: QualityReport):
    n0 = len(df)
    dup = df[id_col].duplicated(keep="first")
    qr.check(dataset, "codigos_duplicados", n0, int(dup.sum()),
             "eliminado (se conserva la 1ª aparición)", f"'{id_col}' debe identificar un único registro")
    return df.loc[~dup].copy()


def _col(cols, candidatos):
    norm = {re.sub(r"[^A-Z0-9]", "", str(c).upper()): c for c in cols}
    for cand in candidatos:
        k = re.sub(r"[^A-Z0-9]", "", cand.upper())
        if k in norm:
            return norm[k]
    return None


def marcar_fuera_de_su_distrito(gdf_pts, gdf_dist, ubigeo_pts, ubigeo_dist,
                                dataset: str, qr: QualityReport, ruta_detalle: Path):
    """No elimina (el polígono puede ser el impreciso): marca `fuera_de_su_distrito`
    y vuelca los marcados a un CSV."""
    import geopandas as gpd
    gdf_pts = gdf_pts.reset_index(drop=True)
    dist = gdf_dist[[ubigeo_dist, "geometry"]].rename(columns={ubigeo_dist: "_ubg_poly"})
    unido = gpd.sjoin(gdf_pts, dist, how="left", predicate="within")
    unido = unido[~unido.index.duplicated(keep="first")].reindex(gdf_pts.index)
    fuera = unido["_ubg_poly"].isna() | (unido["_ubg_poly"].astype(str) != gdf_pts[ubigeo_pts].astype(str))
    gdf_pts = gdf_pts.copy()
    gdf_pts["fuera_de_su_distrito"] = fuera.to_numpy()
    qr.check(dataset, "punto_fuera_de_su_poligono_distrital", len(gdf_pts), int(fuera.sum()),
             "conservado con advertencia",
             "el punto no cae en el polígono del distrito que declara su UBIGEO")
    if fuera.any():
        cols = [x for x in [ubigeo_pts, "NOMBRE", "NOMCP", "DEPARTAMENTO", "DEP", "DISTRITO", "DIST"]
                if x in gdf_pts.columns]
        ruta_detalle.parent.mkdir(parents=True, exist_ok=True)
        gdf_pts.loc[fuera, cols].to_csv(ruta_detalle, index=False, encoding="utf-8-sig")
        print(f"    detalle -> {ruta_detalle.relative_to(REPO_ROOT)}")
    return gdf_pts


# ===========================================================================
# Limpieza de cada dataset
# ===========================================================================
COLS_TEXTO_RENIPRESS = ["NOMBRE", "DIRECCION", "INSTITUCION", "UNIDAD_EJECUTORA",
                        "DEPARTAMENTO", "PROVINCIA", "DISTRITO"]


def limpiar_renipress(renipress_raw, nota_encoding, cfg, qr, distritos):
    import geopandas as gpd
    bbox = cfg["validacion"]["bbox_peru"]
    tol = cfg["validacion"]["tolerancia_coordenada_cero"]
    resolutivas = set(cfg["categorias"]["resolutivas"])
    no_concl = set(cfg["categorias"]["no_concluyentes"])
    activos = {e.upper() for e in cfg["estado_operativo_valido"]}
    ds = "renipress"

    qr.check(ds, "encoding_archivo", 1,
             0 if nota_encoding["encoding_usado"] == cfg["fuentes"]["renipress"]["encoding"] else 1,
             f"leído como {nota_encoding['encoding_usado']}", nota_encoding["detalle"])

    r = renipress_raw.copy()
    r = arreglar_encoding_columnas(r, COLS_TEXTO_RENIPRESS, ds, qr)
    r = validar_coordenadas_xy(r, "NORTE", "ESTE", bbox, tol, ds, qr)
    r = marcar_duplicados(r, "COD_IPRESS", ds, qr)

    r["categoria_norm"] = r["CATEGORIA"].map(normalizar_categoria)
    r["categoria_grupo"] = r["categoria_norm"].map(lambda c: grupo_categoria(c, resolutivas, no_concl))
    qr.check(ds, "categoria_no_reconocida", len(r), int((r["categoria_grupo"] == "desconocida").sum()),
             "conservado con advertencia", "CATEGORIA vacía, '0' o que no calza con el patrón romano+sufijo")

    r["estado_norm"] = r["ESTADO"].map(normalizar_estado)
    r["operativo_activo"] = r["estado_norm"].isin(activos)
    r["resolutiva"] = r["operativo_activo"] & (r["categoria_grupo"] == "resolutiva")

    oferta = gpd.GeoDataFrame(r, geometry=gpd.points_from_xy(r["_lon"], r["_lat"]), crs="EPSG:4326")

    ub_dist = _col(distritos.columns, ["UBIGEO", "IDDIST", "COD_DIST"]) if distritos is not None else None
    logs = ruta(cfg["rutas"]["reports"])
    if distritos is not None and ub_dist:
        oferta = marcar_fuera_de_su_distrito(oferta, distritos, "UBIGEO", ub_dist, ds, qr,
                                             logs / "renipress_fuera_de_poligono.csv")
    else:
        qr.check(ds, "punto_fuera_de_su_poligono_distrital", len(oferta), 0, "no evaluado",
                 "no hay capa de límites distritales en data/raw/")

    print(f"RENIPRESS limpio: {len(oferta)}/{len(renipress_raw)} "
          f"({100*len(oferta)/len(renipress_raw):.1f}%) · resolutivos: {int(oferta['resolutiva'].sum())}")
    return oferta


def limpiar_centros_poblados(centros_raw, cfg, qr, distritos):
    bbox = cfg["validacion"]["bbox_peru"]
    ds = "centros_poblados"
    c = centros_raw.copy()
    c = validar_geometria_puntos(c, bbox, ds, qr)
    cols_txt = [col for col in c.columns if c[col].dtype == object and col != "geometry"]
    c = arreglar_encoding_columnas(c, cols_txt, ds, qr)

    id_cp = _col(c.columns, ["CODCP", "CCPP", "COD_CCPP", "IDCCPP", "CODIGO"])
    if id_cp:
        c = marcar_duplicados(c, id_cp, ds, qr)
    else:
        qr.check(ds, "codigos_duplicados", len(c), 0, "no evaluado",
                 "no se identificó columna de código único")

    ub_dist = _col(distritos.columns, ["UBIGEO", "IDDIST", "COD_DIST"]) if distritos is not None else None
    ub_cp = _col(c.columns, ["UBIGEO", "COD_UBIGEO"])
    logs = ruta(cfg["rutas"]["reports"])
    if distritos is not None and ub_dist and ub_cp:
        c = marcar_fuera_de_su_distrito(c, distritos, ub_cp, ub_dist, ds, qr,
                                        logs / "centros_poblados_fuera_de_poligono.csv")
    print(f"Centros poblados limpio: {len(c)}/{len(centros_raw)} ({100*len(c)/len(centros_raw):.1f}%)")
    return c


# ===========================================================================
# Filtrado de ámbito + almacenamiento
# ===========================================================================
def filtrar_ambito(oferta, demanda, cfg):
    nombres = set(departamentos_objetivo(cfg).values())
    codigos = codigos_inei(cfg)
    of = oferta[oferta["DEPARTAMENTO"].str.upper().isin(nombres)].copy()
    ub_cp = _col(demanda.columns, ["UBIGEO", "COD_UBIGEO"])
    prefijos = tuple(codigos.values())
    dem = (demanda[demanda[ub_cp].astype(str).str.zfill(6).str.startswith(prefijos)].copy()
           if ub_cp else demanda.iloc[0:0])
    return of, dem, ub_cp


def guardar_processed(oferta, demanda, oferta_ambito, demanda_ambito, ub_cp, cfg):
    proc = ruta(cfg["rutas"]["processed"])
    proc.mkdir(parents=True, exist_ok=True)
    oferta.to_file(proc / "renipress_nacional.gpkg", driver="GPKG")
    demanda.to_file(proc / "centros_poblados_nacional.gpkg", driver="GPKG")
    print(f"nacional -> renipress ({len(oferta)}) · centros_poblados ({len(demanda)})")

    codigos = codigos_inei(cfg)
    resumen = []
    for rol, dep in departamentos_objetivo(cfg).items():
        o = oferta_ambito[oferta_ambito["DEPARTAMENTO"].str.upper() == dep]
        d = (demanda_ambito[demanda_ambito[ub_cp].astype(str).str.zfill(6).str.startswith(codigos[dep])]
             if ub_cp else demanda_ambito.iloc[0:0])
        o.to_file(proc / f"renipress_{dep.lower()}.gpkg", driver="GPKG")
        if len(d):
            d.to_file(proc / f"centros_poblados_{dep.lower()}.gpkg", driver="GPKG")
        resumen.append({"rol": rol, "departamento": dep, "establecimientos": len(o),
                        "resolutivos": int(o["resolutiva"].sum()), "centros_poblados": len(d)})
        print(f"  {dep:<9}: renipress {len(o):>4} ({int(o['resolutiva'].sum())} resolutivos) · "
              f"centros_poblados {len(d):>6}")
    return pd.DataFrame(resumen)


def escribir_quality_report(qr: QualityReport, cfg) -> pd.DataFrame:
    logs = ruta(cfg["rutas"]["reports"])
    logs.mkdir(parents=True, exist_ok=True)
    rep = qr.to_frame()
    rep.to_csv(logs / "quality_report.csv", index=False, encoding="utf-8-sig")
    lineas = ["# Informe de Calidad de Datos — Fase 1",
              f"Generado: {datetime.now(timezone.utc).isoformat(timespec='seconds')}", "",
              "| Dataset | Regla | Evaluados | Marcados | % | Acción | Motivo |",
              "|---|---|---:|---:|---:|---|---|"]
    for _, x in rep.iterrows():
        pct = "—" if pd.isna(x["porcentaje"]) else f"{x['porcentaje']}%"
        lineas.append(f"| {x['dataset']} | {x['regla']} | {x['registros_evaluados']} | "
                      f"{x['registros_marcados']} | {pct} | {x['accion']} | {x['motivo'].replace('|', '/')} |")
    (logs / "quality_report.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print(f"Informe -> {(logs / 'quality_report.csv').relative_to(REPO_ROOT)} (+ .md)")
    return rep


# ===========================================================================
# Orquestación
# ===========================================================================
def run(cfg: dict, generar_mapas: bool = True) -> dict:
    import geopandas as gpd
    for clave in ("raw", "processed", "outputs", "reports"):
        ruta(cfg["rutas"][clave]).mkdir(parents=True, exist_ok=True)
    qr = QualityReport()

    cfg_r = cfg["fuentes"]["renipress"]
    renipress_raw, nota = leer_csv_con_encoding(
        ruta(cfg_r["archivo_local"]), cfg_r["separador"], cfg_r["encoding"])
    print(f"RENIPRESS      : {len(renipress_raw)} filas ({nota['encoding_usado']})")

    centros_raw = gpd.read_file(ruta(cfg["fuentes"]["sigmed"]["archivo_local"]))
    centros_raw = centros_raw.set_crs(4326) if centros_raw.crs is None else centros_raw.to_crs(4326)
    print(f"Centros poblados: {len(centros_raw)} filas ({centros_raw.crs})")

    ruta_dist = ruta(cfg["fuentes"]["limites_administrativos"]["capas"]["distrito"]["archivo_local"])
    distritos = None
    if ruta_dist.exists():
        distritos = gpd.read_file(ruta_dist)
        distritos = distritos.set_crs(4326) if distritos.crs is None else distritos.to_crs(4326)
        print(f"Distritos      : {len(distritos)} polígonos")

    oferta = limpiar_renipress(renipress_raw, nota, cfg, qr, distritos)
    demanda = limpiar_centros_poblados(centros_raw, cfg, qr, distritos)
    oferta_ambito, demanda_ambito, ub_cp = filtrar_ambito(oferta, demanda, cfg)
    print(f"Ámbito: oferta {len(oferta_ambito)} · demanda {len(demanda_ambito)}")

    resumen = guardar_processed(oferta, demanda, oferta_ambito, demanda_ambito, ub_cp, cfg)
    rep = escribir_quality_report(qr, cfg)

    if generar_mapas:
        from src.mapas import generar_mapas_departamentos
        generar_mapas_departamentos(oferta_ambito, demanda_ambito, distritos, ub_cp, cfg)

    return {"resumen": resumen.to_dict("records"),
            "quality_report": rep.to_dict("records")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fase 1 (b) — validación, filtrado y almacenamiento")
    ap.add_argument("--sin-mapas", action="store_true", help="omite el Paso 5 (mapas Folium)")
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    try:
        run(cargar_config(a.config), generar_mapas=not a.sin_mapas)
    except FileNotFoundError as e:
        print(f"ERROR: falta un archivo de la Fase 1 (a) -> {e}\n"
              "Corre antes:  python src/acquisition.py")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
