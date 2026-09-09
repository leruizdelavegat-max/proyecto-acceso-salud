# -*- coding: utf-8 -*-
"""
Fase 5 (insumo) — Figuras y números para el informe LaTeX.

Genera, a partir de las salidas de las Fases 1-3:
  - report/figures/*.pdf   (vectoriales, generadas por el pipeline; NO capturas)
  - report/tables/*.tex     (df.to_latex con booktabs)
  - report/tables/_numeros.tex  (\\newcommand con las cifras clave -> el .tex
                                 se rellena solo al recompilar)

    python src/export.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]


def cargar_config():
    import yaml
    with open(REPO_ROOT / "config.md", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _p(rel: str) -> Path:
    return REPO_ROOT / rel


def _fig(fig, nombre: str):
    d = _p("report/figures")
    d.mkdir(parents=True, exist_ok=True)
    fig.savefig(d / f"{nombre}.pdf", bbox_inches="tight")
    fig.savefig(d / f"{nombre}.png", dpi=160, bbox_inches="tight")
    print(f"  figura -> report/figures/{nombre}.pdf")


def _tex(df: pd.DataFrame, nombre: str, caption: str, label: str, float_format="%.1f"):
    d = _p("report/tables")
    d.mkdir(parents=True, exist_ok=True)
    try:
        s = df.to_latex(index=False, escape=True, float_format=float_format,
                        caption=caption, label=label, longtable=False,
                        column_format="l" + "r" * (df.shape[1] - 1))
    except TypeError:
        s = df.to_latex(index=False)
    (d / f"{nombre}.tex").write_text(s, encoding="utf-8")
    print(f"  tabla  -> report/tables/{nombre}.tex")


def haversine_km(lon1, lat1, lon2, lat2):
    r = 6371.0088
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = np.radians(lat2 - lat1), np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def run() -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 9, "figure.dpi": 120, "savefig.bbox": "tight"})

    cfg = cargar_config()
    out = _p(cfg["rutas"]["outputs"])
    bandas = list(cfg.get("metrics", {}).get("bandas_minutos", [30, 60, 120]))
    umbral = int(cfg["routing"].get("umbral_hora_oro_minutos", 60))

    base = pd.read_csv(out / "metricas_por_centro_poblado.csv",
                       dtype={"ubigeo_distrito": str, "cod_ccpp": str})
    cob_dep = pd.read_csv(out / "cobertura_bandas_por_departamento.csv")
    gini = pd.read_csv(out / "desigualdad_gini.csv")
    lorenz = pd.read_csv(out / "curva_lorenz.csv")
    cross = pd.read_csv(out / "cross_acceso_altitud.csv")
    brechas = pd.read_csv(out / "brechas_criticas_distritos.csv")
    acc_dep = pd.read_csv(out / "acceso_ponderado_departamento.csv")

    rut = base.dropna(subset=["t_min"]).copy()
    macros = {}

    # ---- Fig 1: cobertura por banda y departamento (barras apiladas) ----
    piv = cob_dep.pivot_table(index="departamento", columns="banda",
                              values="share_pct", observed=True).fillna(0)
    orden = [f"0-{bandas[0]}"] + [f"{bandas[i]}-{bandas[i+1]}" for i in range(len(bandas) - 1)] \
        + [f">{bandas[-1]}", "sin_ruta"]
    piv = piv.reindex(columns=[c for c in orden if c in piv.columns])
    fig, ax = plt.subplots(figsize=(6, 3))
    piv.plot(kind="barh", stacked=True, ax=ax,
             color=["#1a9850", "#a6d96a", "#fdae61", "#d73027", "#7f7f7f"][:piv.shape[1]])
    ax.set_xlabel("% de población"); ax.set_ylabel("")
    ax.legend(title="Minutos en auto", bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=7)
    _fig(fig, "cobertura_bandas"); plt.close(fig)

    # ---- Fig 2: ECDF del tiempo de acceso, urbano vs rural ----
    fig, ax = plt.subplots(figsize=(5, 3))
    for ur, g in rut.groupby("urbano_rural"):
        g = g.sort_values("t_min")
        cw = np.cumsum(g["poblacion"]) / g["poblacion"].sum()
        ax.plot(g["t_min"], cw, label=ur, lw=1.6)
    ax.axvline(umbral, color="red", ls="--", lw=1, label=f"{umbral} min")
    ax.set_xlabel("Tiempo de acceso en auto (min)")
    ax.set_ylabel("Proporción acumulada de población")
    ax.set_xlim(0, min(240, rut["t_min"].quantile(0.98))); ax.legend(fontsize=8)
    _fig(fig, "ecdf_urbano_rural"); plt.close(fig)

    # ---- Fig 3: curva de Lorenz del acceso ----
    fig, ax = plt.subplots(figsize=(4, 4))
    ax.plot(lorenz["pob_acumulada"], lorenz["tiempo_acumulado"], lw=1.8, label="Lorenz (acceso)")
    ax.plot([0, 1], [0, 1], color="#999", ls="--", lw=1, label="Igualdad perfecta")
    ax.set_xlabel("Proporción acumulada de población")
    ax.set_ylabel("Proporción acumulada del tiempo de acceso")
    g3 = gini.loc[gini["ambito"].str.contains("3 depart", case=False), "gini_acceso"]
    if len(g3):
        ax.set_title(f"Gini = {float(g3.iloc[0]):.3f}", fontsize=9)
    ax.legend(fontsize=8)
    _fig(fig, "lorenz"); plt.close(fig)

    # ---- Fig 4: acceso vs altitud ----
    fig, ax = plt.subplots(figsize=(5, 3))
    c = cross.dropna(subset=["acceso_min_ponderado"])
    ax.bar(c["franja_altitud"].astype(str), c["acceso_min_ponderado"], color="#4575b4")
    ax.set_ylabel("Acceso medio ponderado (min)"); ax.set_xlabel("")
    ax.tick_params(axis="x", rotation=20)
    _fig(fig, "acceso_altitud"); plt.close(fig)

    # ---- Fig 5: línea recta vs red (discusión Fase 2) ----
    try:
        import geopandas as gpd
        acc = pd.read_parquet(out / "acceso_nearest.parquet")
        acc = acc[(acc["profile"] == "car") & acc["routable"]].copy()
        acc["cod_ccpp"] = acc["cod_ccpp"].astype(str)
        acc["cod_destino_cercano"] = acc["cod_destino_cercano"].astype(str)

        g = gpd.read_file(_p(cfg["routing"]["rutas"]["demanda_muestreada"]))
        g_xy = pd.DataFrame({"cod_ccpp": g["CODCP"].astype(str),
                             "cx": g.geometry.x.to_numpy(), "cy": g.geometry.y.to_numpy()})
        of = gpd.read_file(_p("data/processed/renipress_nacional.gpkg"))
        of_xy = pd.DataFrame({"cod_destino_cercano": of["COD_IPRESS"].astype(str),
                              "fx": of.geometry.x.to_numpy(), "fy": of.geometry.y.to_numpy()})
        acc = acc.merge(g_xy, on="cod_ccpp", how="inner").merge(
            of_xy, on="cod_destino_cercano", how="inner")
        acc["recta_km"] = haversine_km(acc["cx"].to_numpy(), acc["cy"].to_numpy(),
                                       acc["fx"].to_numpy(), acc["fy"].to_numpy())
        acc = acc[(acc["recta_km"] > 0.2) & (acc["distance_km"] > 0)]
        factor = float((acc["distance_km"] / acc["recta_km"]).median())
        macros["factorRodeo"] = f"{factor:.2f}"
        fig, ax = plt.subplots(figsize=(4.2, 4.2))
        ax.scatter(acc["recta_km"], acc["distance_km"], s=4, alpha=0.25, color="#2166ac")
        lim = np.nanpercentile(acc["distance_km"], 99)
        ax.plot([0, lim], [0, lim], color="#999", ls="--", lw=1, label="1:1 (línea recta)")
        ax.plot([0, lim], [0, lim * factor], color="#d73027", lw=1.4,
                label=f"factor de rodeo mediano = {factor:.2f}")
        ax.set_xlim(0, lim); ax.set_ylim(0, lim)
        ax.set_xlabel("Distancia en línea recta (km)")
        ax.set_ylabel("Distancia por carretera (km)")
        ax.legend(fontsize=8)
        _fig(fig, "recta_vs_red"); plt.close(fig)
    except Exception as e:
        print(f"  (fig recta_vs_red omitida: {e})")

    # ---- Tablas .tex ----
    _tex(cob_dep.pivot_table(index="departamento", columns="banda", values="share_pct",
                             observed=True).fillna(0).round(1).reset_index(),
         "cobertura_bandas", "Cobertura poblacional (\\%) por banda de tiempo de acceso en auto.",
         "tab:cobertura")
    _tex(acc_dep[["departamento", "poblacion", "poblacion_ruteable",
                  "pct_sin_ruta", "acceso_min_ponderado"]].round(1),
         "acceso_departamento", "Acceso medio ponderado por población, por departamento.",
         "tab:acceso-dep")
    _tex(brechas[["rank", "nombre_distrito", "poblacion", "pct_sin_ruta",
                  "acceso_min_ponderado"]].head(15),
         "brechas_criticas", "Los 15 distritos con peor acceso ponderado por población.",
         "tab:brechas")
    _tex(gini.round(3), "gini", "Coeficiente de Gini del tiempo de acceso ponderado por población.",
         "tab:gini")

    # contraste urbano/rural
    try:
        ur = pd.read_csv(out / "contraste_urbano_rural.csv")
        _tex(ur.round(1), "urbano_rural",
             "Acceso y cobertura por clasificación urbano/rural (regla INEI) y departamento.",
             "tab:urbano-rural")
    except Exception:
        pass

    # informe de calidad de datos de la Fase 1
    try:
        q = pd.read_csv(_p("logs/quality_report.csv"))
        qs = q[["dataset", "regla", "registros_marcados", "porcentaje", "accion"]].copy()
        qs["regla"] = qs["regla"].str.replace("_", " ", regex=False)
        qs["dataset"] = qs["dataset"].str.replace("_", " ", regex=False)
        _tex(qs, "calidad_datos",
             "Informe de calidad de datos de la Fase 1: registros marcados por regla.",
             "tab:calidad")
    except Exception as e:
        print(f"  (tabla calidad_datos omitida: {e})")

    # ---- Números para el texto ----
    pob_tot = base["poblacion"].sum()
    pob_cub = base.loc[base["t_min"] <= umbral, "poblacion"].sum()
    pob_sin = base.loc[base["t_min"].isna(), "poblacion"].sum()
    macros.update({
        "umbralOro": str(umbral),
        "nCentrosPoblados": f"{len(base):,}".replace(",", "\\,"),
        "poblacionTotal": f"{pob_tot:,.0f}".replace(",", "\\,"),
        "pctCubierta": f"{100*pob_cub/pob_tot:.1f}",
        "pctSinRuta": f"{100*pob_sin/pob_tot:.1f}",
        "poblacionSinRuta": f"{pob_sin:,.0f}".replace(",", "\\,"),
        "medianaAcceso": f"{rut['t_min'].median():.0f}",
        "giniTotal": f"{float(gini.iloc[0]['gini_acceso']):.3f}" if len(gini) else "--",
        "spearmanAltitud": (f"{rut[['t_min', 'altitud_m']].corr(method='spearman').iloc[0, 1]:.2f}"
                            if rut["altitud_m"].notna().any() else "--"),
        "peorDistrito": str(brechas.iloc[0]["nombre_distrito"]) if len(brechas) else "--",
        "fraccionMuestra": "29",
    })
    tex = "\n".join(f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in macros.items()) + "\n"
    _p("report/tables").mkdir(parents=True, exist_ok=True)
    (_p("report/tables/_numeros.tex")).write_text(tex, encoding="utf-8")
    print("  números -> report/tables/_numeros.tex")
    print("\n".join(f"    {k} = {v}" for k, v in macros.items()))
    return macros


def main() -> int:
    try:
        run()
    except FileNotFoundError as e:
        print(f"ERROR: falta una salida de la Fase 3 -> {e}\n"
              "Corre antes:  python src/routing.py --all && python src/metrics.py", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
