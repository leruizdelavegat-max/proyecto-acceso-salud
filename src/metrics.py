# -*- coding: utf-8 -*-
"""
Fase 3 — Construcción de métricas y análisis.

Convierte los tiempos de viaje de la Fase 2 en indicadores para decisión:

  1. t_min(i)        — minutos en auto de cada centro poblado al establecimiento
                       resolutivo más cercano.
  2. Bandas de cobertura — % de población a 0-30 / 30-60 / 60-120 / >120 min
                       (y sin ruta), a nivel nacional y por departamento.
  3. Acceso medio ponderado por población, agregado a distrito, provincia y
     departamento.
  4. Lista de brechas críticas — distritos con el peor acceso ponderado.
  5. Desigualdad del acceso — coeficiente de Gini + curva de Lorenz del tiempo
     de acceso ponderado por población.
  6. Contraste urbano / rural con una regla explícita (INEI: >= 2000 hab. o
     capital distrital/provincial).

  Cross-análisis: acceso vs. altitud (columna `Z` de los centros poblados).
  Se reporta como correlacional, no causal (ver run()).

Todas las funciones toman un DataFrame y devuelven un DataFrame (sin efectos
de lado). `run()` orquesta, exporta CSV a data/outputs/ y tablas .tex a
report/tables/.

    python src/metrics.py            # corre todo
    from src.metrics import gini_ponderado, bandas_cobertura, ...
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import geopandas as gpd
except Exception:  # pragma: no cover
    gpd = None

REPO_ROOT = Path(__file__).resolve().parents[1]


# ===========================================================================
# Config
# ===========================================================================
def cargar_config(path: Path | str | None = None) -> dict:
    import yaml
    path = Path(path) if path else REPO_ROOT / "config.md"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ruta(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else REPO_ROOT / p


# ===========================================================================
# Funciones puras — métricas (DataFrame -> DataFrame)
# ===========================================================================
def clasificar_bandas(t_min: pd.Series, bandas: list[int]) -> pd.Series:
    """Etiqueta cada tiempo de acceso en una banda. NaN -> 'sin_ruta'."""
    cortes = [-np.inf] + list(bandas) + [np.inf]
    etiquetas = ([f"0-{bandas[0]}"]
                 + [f"{bandas[i]}-{bandas[i+1]}" for i in range(len(bandas) - 1)]
                 + [f">{bandas[-1]}"])
    b = pd.cut(t_min, bins=cortes, labels=etiquetas, right=False)
    return b.cat.add_categories(["sin_ruta"]).fillna("sin_ruta")


def bandas_cobertura(df: pd.DataFrame, bandas: list[int],
                     col_t="t_min", col_pob="poblacion", grupo: str | None = None
                     ) -> pd.DataFrame:
    """% de población por banda de tiempo de acceso. Si `grupo` se pasa (p.ej.
    'departamento'), desagrega por ese campo."""
    d = df.copy()
    d["banda"] = clasificar_bandas(d[col_t], bandas)
    claves = ([grupo] if grupo else []) + ["banda"]
    g = d.groupby(claves, observed=True)[col_pob].sum().reset_index(name="poblacion")
    tot = d.groupby([grupo], observed=True)[col_pob].sum() if grupo else d[col_pob].sum()
    if grupo:
        g["poblacion_total_grupo"] = g[grupo].map(tot)
        g["share_pct"] = (100 * g["poblacion"] / g["poblacion_total_grupo"]).round(2)
    else:
        g["share_pct"] = (100 * g["poblacion"] / tot).round(2)
    return g


def acceso_ponderado(df: pd.DataFrame, col_ubigeo="ubigeo_distrito",
                     col_t="t_min", col_pob="poblacion", col_nombre="nombre_distrito"
                     ) -> pd.DataFrame:
    """Acceso medio ponderado por población a un nivel administrativo.
    El promedio se toma sobre la población CON ruta; se reporta aparte el
    % de población sin ruta (no se le imputa distancia en línea recta)."""
    d = df.copy()
    d["_ruteable"] = d[col_t].notna()
    d["_pt"] = d[col_t] * d[col_pob]

    def _agg(g):
        pob = g[col_pob].sum()
        pob_r = g.loc[g["_ruteable"], col_pob].sum()
        acc = g.loc[g["_ruteable"], "_pt"].sum() / pob_r if pob_r else np.nan
        return pd.Series({
            "poblacion": pob,
            "poblacion_ruteable": pob_r,
            "pct_sin_ruta": round(100 * (pob - pob_r) / pob, 2) if pob else np.nan,
            "acceso_min_ponderado": round(acc, 2) if pd.notna(acc) else np.nan,
            "n_centros_poblados": len(g),
        })

    cols = [col_ubigeo] + ([col_nombre] if col_nombre in d.columns else [])
    out = d.groupby(cols, observed=True).apply(_agg, include_groups=False).reset_index()
    return out.sort_values("acceso_min_ponderado", ascending=False, na_position="first")


def brechas_criticas(df_nivel: pd.DataFrame, n: int = 20,
                     col_acc="acceso_min_ponderado") -> pd.DataFrame:
    """Los `n` distritos con peor acceso ponderado (los sin ruta primero)."""
    d = df_nivel.copy()
    d["_sin_dato"] = d[col_acc].isna()
    d = d.sort_values(["_sin_dato", col_acc], ascending=[False, False])
    d["rank"] = range(1, len(d) + 1)
    return d.drop(columns="_sin_dato").head(n).reset_index(drop=True)


def gini_ponderado(valores: np.ndarray, pesos: np.ndarray) -> float:
    """Gini ponderado por población de una magnitud no negativa (aquí el
    tiempo de acceso). 0 = todos igual; ->1 = muy desigual.
    Se usa Gini (y no solo la varianza) porque es un escalar acotado [0,1],
    comparable entre departamentos y en el tiempo, y estándar en la literatura
    de equidad en el acceso a servicios."""
    v = np.asarray(valores, dtype=float)
    w = np.asarray(pesos, dtype=float)
    m = np.isfinite(v) & np.isfinite(w) & (w > 0)
    v, w = v[m], w[m]
    if len(v) < 2 or v.sum() == 0:
        return float("nan")
    orden = np.argsort(v)
    v, w = v[orden], w[orden]
    wn = w / w.sum()
    cum_w = np.concatenate([[0.0], np.cumsum(wn)])
    cum_vw = np.concatenate([[0.0], np.cumsum(v * wn) / np.sum(v * wn)])
    # Gini = 1 - 2 * (área bajo la curva de Lorenz)
    trapz = getattr(np, "trapezoid", getattr(np, "trapz", None))
    B = trapz(cum_vw, cum_w)
    return float(round(1 - 2 * B, 4))


def curva_lorenz(valores: np.ndarray, pesos: np.ndarray, n_puntos: int = 100
                 ) -> pd.DataFrame:
    """Puntos (share acumulado de población, share acumulado del tiempo de
    acceso) para graficar la curva de Lorenz en el informe."""
    v = np.asarray(valores, dtype=float)
    w = np.asarray(pesos, dtype=float)
    m = np.isfinite(v) & np.isfinite(w) & (w > 0)
    v, w = v[m], w[m]
    orden = np.argsort(v)
    v, w = v[orden], w[orden]
    wn = w / w.sum()
    cum_w = np.concatenate([[0], np.cumsum(wn)])
    cum_v = np.concatenate([[0], np.cumsum(v * wn) / np.sum(v * wn)])
    xs = np.linspace(0, 1, n_puntos)
    return pd.DataFrame({"pob_acumulada": xs,
                         "tiempo_acumulado": np.interp(xs, cum_w, cum_v),
                         "igualdad_perfecta": xs})


def clasificar_urbano_rural(df: pd.DataFrame, umbral_poblacion: int = 2000,
                            col_pob="poblacion", col_capital="CAPITAL") -> pd.Series:
    """Regla INEI (explícita): un centro poblado es **urbano** si tiene
    >= `umbral_poblacion` habitantes O es capital de distrito/provincia
    (`CAPITAL` == '1'); si no, es **rural**."""
    pob_ok = df[col_pob].fillna(0) >= umbral_poblacion
    cap_ok = (df[col_capital].astype(str).isin(["1", "1.0", "True", "SI", "Sí"])
              if col_capital in df.columns else pd.Series(False, index=df.index))
    return np.where(pob_ok | cap_ok, "urbano", "rural")


def contraste_urbano_rural(df: pd.DataFrame, bandas: list[int],
                           col_t="t_min", col_pob="poblacion", col_ur="urbano_rural",
                           col_dep="departamento") -> pd.DataFrame:
    """Acceso ponderado y cobertura <60 min por urbano/rural y departamento."""
    d = df.copy()
    d["_ruteable"] = d[col_t].notna()
    d["_pt"] = d[col_t] * d[col_pob]
    umbral = bandas[1] if len(bandas) > 1 else 60
    d["_dentro_umbral"] = d[col_t] <= umbral

    def _agg(g):
        pob = g[col_pob].sum()
        pob_r = g.loc[g["_ruteable"], col_pob].sum()
        return pd.Series({
            "poblacion": pob,
            "acceso_min_ponderado": round(g.loc[g["_ruteable"], "_pt"].sum() / pob_r, 2) if pob_r else np.nan,
            f"pct_pob_<= {umbral}min": round(100 * g.loc[g["_dentro_umbral"], col_pob].sum() / pob, 2) if pob else np.nan,
            "pct_sin_ruta": round(100 * (pob - pob_r) / pob, 2) if pob else np.nan,
        })

    return (d.groupby([col_dep, col_ur], observed=True)
            .apply(_agg, include_groups=False).reset_index())


def cross_acceso_altitud(df: pd.DataFrame, col_t="t_min", col_alt="altitud_m",
                         col_pob="poblacion",
                         bandas_alt=(0, 500, 1500, 3500, 9000)) -> pd.DataFrame:
    """Cross-análisis: acceso ponderado por franja altitudinal + correlación
    (Spearman) entre t_min y altitud. La relación es **correlacional, no
    causal**: la altitud es un proxy de terreno difícil y dispersión de la
    población (Andes), no una causa directa de la distancia por carretera."""
    d = df.copy()
    etiquetas = ["costa <500", "yungas 500-1500", "sierra 1500-3500", "puna >3500"]
    d["franja_altitud"] = pd.cut(d[col_alt], bins=list(bandas_alt), labels=etiquetas, right=False)
    d["_ruteable"] = d[col_t].notna()
    d["_pt"] = d[col_t] * d[col_pob]

    def _agg(g):
        pr = g.loc[g["_ruteable"], col_pob].sum()
        return pd.Series({
            "poblacion": g[col_pob].sum(),
            "acceso_min_ponderado": round(g.loc[g["_ruteable"], "_pt"].sum() / pr, 2) if pr else np.nan,
            "altitud_media_m": round(g[col_alt].mean(), 0),
        })

    tabla = d.groupby("franja_altitud", observed=True).apply(_agg, include_groups=False).reset_index()

    r = d.loc[d["_ruteable"], [col_t, col_alt]].corr(method="spearman").iloc[0, 1]
    tabla.attrs["spearman_t_min_vs_altitud"] = round(float(r), 3)
    tabla.attrs["interpretacion"] = "correlacional, no causal"
    return tabla


# ===========================================================================
# Ensamblado de la tabla base por centro poblado
# ===========================================================================
def tabla_base(acceso: pd.DataFrame, demanda: "gpd.GeoDataFrame",
               poblacion: pd.DataFrame | None, perfil_principal: str = "car"
               ) -> pd.DataFrame:
    """Une acceso (Fase 2) + atributos del centro poblado + población en una
    sola tabla por `cod_ccpp`, con columnas: t_min, distancia_km, routable,
    ubigeo/nombre de distrito-provincia-departamento, altitud_m, poblacion,
    urbano_rural."""
    acc = acceso[acceso["profile"] == perfil_principal].copy()
    acc["t_min"] = np.where(acc["routable"], acc["duration_min"], np.nan)
    acc = acc[["cod_ccpp", "t_min", "distance_km", "routable", "cod_destino_cercano"]]

    dem = demanda.copy()
    dem["cod_ccpp"] = dem["CODCP"].astype(str)
    acc["cod_ccpp"] = acc["cod_ccpp"].astype(str)
    dem["ubigeo_distrito"] = dem["UBIGEO"].astype(str).str.zfill(6)
    dem["ubigeo_provincia"] = dem["ubigeo_distrito"].str[:4]
    dem["ubigeo_departamento"] = dem["ubigeo_distrito"].str[:2]
    for a, b in (("DIST", "nombre_distrito"), ("PROV", "nombre_provincia"),
                 ("DEP", "departamento")):
        if a in dem.columns:
            dem[b] = dem[a]
    dem["altitud_m"] = pd.to_numeric(dem.get("Z"), errors="coerce")
    cols_dem = ["cod_ccpp", "NOMCP", "CAPITAL", "ubigeo_distrito", "ubigeo_provincia",
                "ubigeo_departamento", "nombre_distrito", "nombre_provincia",
                "departamento", "altitud_m"]
    cols_dem = [c for c in cols_dem if c in dem.columns]

    base = dem[cols_dem].merge(acc, on="cod_ccpp", how="left")

    if poblacion is not None and len(poblacion):
        p = poblacion.rename(columns={poblacion.columns[0]: "cod_ccpp"}).copy()
        p["cod_ccpp"] = p["cod_ccpp"].astype(str)
        base = base.merge(p[["cod_ccpp", "poblacion"]], on="cod_ccpp", how="left")
        base["poblacion"] = base["poblacion"].fillna(0.0)
        base.attrs["fuente_poblacion"] = "raster (ver src/poblacion.py)"
    else:
        base["poblacion"] = 1.0
        base.attrs["fuente_poblacion"] = (
            "UNIFORME (1 por centro poblado) — NO hay fuente de población; "
            "las métricas 'ponderadas' son provisionales. Ver Limitaciones.")

    base["urbano_rural"] = clasificar_urbano_rural(base)
    return base


# ===========================================================================
# Orquestación
# ===========================================================================
def _to_latex(df: pd.DataFrame, ruta: Path, caption: str, label: str,
              float_format="%.1f") -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    try:
        tex = df.to_latex(index=False, escape=True, float_format=float_format,
                          caption=caption, label=label, longtable=False)
    except Exception:
        tex = df.to_latex(index=False)
    ruta.write_text(tex, encoding="utf-8")


def run(cfg: dict) -> dict:
    if gpd is None:
        raise RuntimeError("geopandas no está instalado.")
    mcfg = cfg.get("metrics", {})
    rcfg = cfg["routing"]
    bandas = list(mcfg.get("bandas_minutos", rcfg.get("bandas_minutos", [30, 60, 120])))
    n_brechas = int(mcfg.get("n_brechas", 20))
    umbral_urbano = int(mcfg.get("umbral_urbano_habitantes", 2000))
    perfil = rcfg.get("perfil_principal", "car")

    out = _ruta(cfg["rutas"]["outputs"])
    out.mkdir(parents=True, exist_ok=True)
    tablas = _ruta("report/tables")

    acceso = pd.read_parquet(_ruta(rcfg["rutas"]["cache_acceso"]))
    demanda = gpd.read_file(_ruta(rcfg["rutas"]["demanda_muestreada"]))
    ruta_pob = _ruta(mcfg.get("archivo_poblacion", "data/processed/centros_poblados_poblacion.parquet"))
    poblacion = pd.read_parquet(ruta_pob) if ruta_pob.exists() else None

    base = tabla_base(acceso, demanda, poblacion, perfil)
    print(f"tabla base: {len(base)} centros poblados · población: {base.attrs['fuente_poblacion']}")
    base.to_csv(out / "metricas_por_centro_poblado.csv", index=False, encoding="utf-8-sig")

    # 2. bandas de cobertura
    cob_nac = bandas_cobertura(base, bandas)
    cob_dep = bandas_cobertura(base, bandas, grupo="departamento")
    cob_nac.to_csv(out / "cobertura_bandas.csv", index=False, encoding="utf-8-sig")
    cob_dep.to_csv(out / "cobertura_bandas_por_departamento.csv", index=False, encoding="utf-8-sig")

    # 3. acceso ponderado por nivel
    niveles = {
        "distrito": ("ubigeo_distrito", "nombre_distrito"),
        "provincia": ("ubigeo_provincia", "nombre_provincia"),
        "departamento": ("ubigeo_departamento", "departamento"),
    }
    pond = {}
    for nombre, (col_u, col_n) in niveles.items():
        p = acceso_ponderado(base, col_ubigeo=col_u, col_nombre=col_n)
        p.to_csv(out / f"acceso_ponderado_{nombre}.csv", index=False, encoding="utf-8-sig")
        pond[nombre] = p

    # 4. brechas críticas (distrito)
    brechas = brechas_criticas(pond["distrito"], n_brechas)
    brechas.to_csv(out / "brechas_criticas_distritos.csv", index=False, encoding="utf-8-sig")
    _to_latex(brechas[["rank", "nombre_distrito", "poblacion", "pct_sin_ruta",
                       "acceso_min_ponderado"]].head(15),
              tablas / "brechas_criticas.tex",
              "Distritos con peor acceso ponderado por población al establecimiento resolutivo más cercano (auto).",
              "tab:brechas")

    # 5. desigualdad: Gini + Lorenz (nacional y por departamento)
    filas_gini = []
    br = base.dropna(subset=["t_min"])
    filas_gini.append({"ambito": "3 departamentos", "gini_acceso": gini_ponderado(br["t_min"], br["poblacion"]),
                       "poblacion_ruteable": br["poblacion"].sum()})
    for dep, g in br.groupby("departamento"):
        filas_gini.append({"ambito": dep, "gini_acceso": gini_ponderado(g["t_min"], g["poblacion"]),
                           "poblacion_ruteable": g["poblacion"].sum()})
    gini_df = pd.DataFrame(filas_gini)
    gini_df.to_csv(out / "desigualdad_gini.csv", index=False, encoding="utf-8-sig")
    curva_lorenz(br["t_min"], br["poblacion"]).to_csv(
        out / "curva_lorenz.csv", index=False, encoding="utf-8-sig")

    # 6. urbano / rural
    ur = contraste_urbano_rural(base, bandas)
    ur.to_csv(out / "contraste_urbano_rural.csv", index=False, encoding="utf-8-sig")

    # cross-análisis: acceso vs altitud
    cross = cross_acceso_altitud(base)
    cross.to_csv(out / "cross_acceso_altitud.csv", index=False, encoding="utf-8-sig")

    _to_latex(cob_dep, tablas / "cobertura_bandas.tex",
              "Cobertura poblacional por banda de tiempo de acceso, por departamento.", "tab:cobertura")
    _to_latex(pond["departamento"], tablas / "acceso_departamento.tex",
              "Acceso medio ponderado por población, por departamento.", "tab:acceso-dep")

    resumen = {
        "n_centros_poblados": len(base),
        "fuente_poblacion": base.attrs["fuente_poblacion"],
        "pct_sin_ruta": round(100 * base["t_min"].isna().mean(), 2),
        "acceso_min_ponderado_3dep": float(pond["departamento"]["acceso_min_ponderado"].mean()),
        "gini_3dep": gini_df.iloc[0]["gini_acceso"],
        "spearman_acceso_altitud": cross.attrs.get("spearman_t_min_vs_altitud"),
        "salidas": sorted(p.name for p in out.glob("*.csv")),
        "tablas_tex": sorted(p.name for p in tablas.glob("*.tex")) if tablas.exists() else [],
    }
    print("\n=== Fase 3 — resumen ===")
    for k, v in resumen.items():
        print(f"  {k}: {v}")
    return resumen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fase 3 — métricas de acceso")
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    cfg = cargar_config(a.config)
    try:
        run(cfg)
    except FileNotFoundError as e:
        print(f"ERROR: falta un insumo de la Fase 2 -> {e}\n"
              "Corre antes:  python src/routing.py --all", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
