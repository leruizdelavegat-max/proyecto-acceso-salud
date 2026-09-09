# -*- coding: utf-8 -*-
"""
Fase 4 — Dashboard Streamlit "La hora dorada".

Lee SOLO archivos precalculados (Fases 1-3). No rutea ni construye grafos.

    streamlit run app.py

Vistas: KPIs · mapa coroplético de acceso por distrito + capa de
establecimientos · distribución (ECDF) del tiempo de acceso · tabla de
distritos rankeada con descarga CSV · simulador de escenario (subir
establecimientos I-3/I-4 a resolutivos y ver la ganancia de cobertura) ·
panel de calidad de datos de la Fase 1.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
import yaml

# La lógica de métricas vive en src/metrics.py (el enunciado lo exige):
from src.metrics import bandas_cobertura, clasificar_bandas

REPO_ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="La hora dorada", page_icon="🏥", layout="wide")


def _p(rel: str) -> Path:
    return REPO_ROOT / rel


@st.cache_data
def cargar_config() -> dict:
    with open(_p("config.md"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


CFG = cargar_config()
BANDAS = list(CFG.get("metrics", {}).get("bandas_minutos", [30, 60, 120]))
UMBRAL_DEFECTO = int(CFG["routing"].get("umbral_hora_oro_minutos", 60))
CATS_RESOLUTIVAS = set(CFG["categorias"]["resolutivas"])


@st.cache_data
def cargar_base() -> pd.DataFrame | None:
    f = _p("data/outputs/metricas_por_centro_poblado.csv")
    if not f.exists():
        return None
    df = pd.read_csv(f, dtype={"ubigeo_distrito": str, "ubigeo_provincia": str,
                               "ubigeo_departamento": str, "cod_ccpp": str})
    return df


@st.cache_data
def cargar_matriz() -> pd.DataFrame | None:
    f = _p("data/outputs/matriz_tiempos.parquet")
    if not f.exists():
        return None
    m = pd.read_parquet(f)
    m = m[m["profile"] == "car"].copy()
    m["cod_ccpp"] = m["cod_ccpp"].astype(str)
    m["cod_ipress"] = m["cod_ipress"].astype(str)
    m["duration_min"] = m["duration_s"] / 60.0
    return m


@st.cache_data
def cargar_oferta() -> "pd.DataFrame":
    import geopandas as gpd
    f = _p("data/processed/renipress_nacional.gpkg")
    g = gpd.read_file(f)
    nombres = {CFG["departamentos"]["costero"], CFG["departamentos"]["andino"],
               CFG["departamentos"]["amazonico"]}
    g = g[g["DEPARTAMENTO"].str.upper().isin(nombres)].copy()
    g["lon"], g["lat"] = g.geometry.x, g.geometry.y
    g["cod_ipress"] = g["COD_IPRESS"].astype(str)
    g["resolutiva"] = g["resolutiva"].astype("boolean").fillna(False).astype(bool)
    inst = g.get("INSTITUCION", pd.Series("", index=g.index)).fillna("").str.upper()
    g["grupo_institucion"] = np.select(
        [inst.str.contains("MINSA|GOBIERNO REGIONAL|REGIÓN|GOB.REG", regex=True),
         inst.str.contains("ESSALUD|SEGURO SOCIAL", regex=True),
         inst.str.contains("PRIVAD", regex=True),
         inst.str.contains("SANIDAD|FUERZAS ARMADAS|PNP|POLIC", regex=True)],
        ["MINSA / GORE", "EsSalud", "Privado", "Sanidad FF.AA./PNP"], default="Otro")
    return g[["cod_ipress", "NOMBRE", "DEPARTAMENTO", "PROVINCIA", "DISTRITO",
              "CATEGORIA", "categoria_norm", "categoria_grupo", "grupo_institucion",
              "resolutiva", "lon", "lat"]]


@st.cache_data
def cargar_distritos_geojson() -> str | None:
    import geopandas as gpd
    f = _p("data/raw/limites_distrito.gpkg")
    if not f.exists():
        return None
    g = gpd.read_file(f)
    codigos = set(CFG["departamentos"]["codigos_inei"].values())
    g = g[g["ccdd"].astype(str).str.zfill(2).isin(codigos)].copy()
    g["geometry"] = g.geometry.simplify(0.005, preserve_topology=True)
    return g[["ubigeo", "nombdist", "nombprov", "nombdep", "geometry"]].to_json()


@st.cache_data
def cargar_calidad() -> pd.DataFrame | None:
    f = _p("logs/quality_report.csv")
    return pd.read_csv(f) if f.exists() else None


# ---------------------------------------------------------------------------
def acceso_por_distrito(base: pd.DataFrame) -> pd.DataFrame:
    """Acceso medio ponderado por población y cobertura <umbral por distrito."""
    d = base.copy()
    d["_r"] = d["t_min"].notna()
    d["_pt"] = d["t_min"] * d["poblacion"]

    def _agg(g):
        pob = g["poblacion"].sum()
        pr = g.loc[g["_r"], "poblacion"].sum()
        return pd.Series({
            "departamento": g["departamento"].iloc[0],
            "provincia": g["nombre_provincia"].iloc[0] if "nombre_provincia" in g else "",
            "poblacion": pob,
            "acceso_min_ponderado": round(g.loc[g["_r"], "_pt"].sum() / pr, 1) if pr else np.nan,
            "pct_sin_ruta": round(100 * (pob - pr) / pob, 1) if pob else np.nan,
        })

    out = (d.groupby(["ubigeo_distrito", "nombre_distrito"], observed=True)
           .apply(_agg, include_groups=False).reset_index())
    return out


def _mapa_coropletico(distritos_geojson: str, acc_dist: pd.DataFrame,
                      oferta: pd.DataFrame, capas_oferta: list[str]) -> str:
    import folium
    import branca

    acc = acc_dist.set_index("ubigeo_distrito")
    vals = acc["acceso_min_ponderado"].dropna()
    vmax = float(np.nanpercentile(vals, 95)) if len(vals) else 120.0
    cmap = branca.colormap.LinearColormap(
        ["#1a9850", "#fee08b", "#d73027", "#7f0000"], vmin=0, vmax=vmax,
        caption="Acceso medio en auto al establecimiento resolutivo (min, ponderado por población)")

    import json
    gj = json.loads(distritos_geojson)
    for feat in gj["features"]:
        ub = str(feat["properties"].get("ubigeo"))
        r = acc.loc[ub] if ub in acc.index else None
        feat["properties"]["acceso"] = None if r is None or pd.isna(r["acceso_min_ponderado"]) else float(r["acceso_min_ponderado"])
        feat["properties"]["poblacion"] = 0 if r is None else int(r["poblacion"])
        feat["properties"]["pct_sin_ruta"] = None if r is None else float(r["pct_sin_ruta"])

    centro = [-9.5, -75.5]
    m = folium.Map(location=centro, zoom_start=6, control_scale=True,
                   tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
                   attr="Esri Light Gray")

    def _style(feat):
        v = feat["properties"]["acceso"]
        return {"fillColor": "#cccccc" if v is None else cmap(v),
                "color": "#666", "weight": 0.6, "fillOpacity": 0.75}

    folium.GeoJson(
        gj, name="Acceso por distrito", style_function=_style,
        highlight_function=lambda f: {"weight": 2, "color": "#000"},
        tooltip=folium.GeoJsonTooltip(
            fields=["nombdist", "nombdep", "acceso", "poblacion", "pct_sin_ruta"],
            aliases=["Distrito", "Departamento", "Acceso (min)", "Población", "% sin ruta"],
            localize=True)).add_to(m)
    cmap.add_to(m)

    colores = {"Resolutivos II-1+": ("#d00000", oferta[oferta["resolutiva"]]),
               "No resolutivos I-1 a I-4": ("#8d99ae", oferta[~oferta["resolutiva"]])}
    for nombre, (color, sub) in colores.items():
        if nombre not in capas_oferta or not len(sub):
            continue
        fg = folium.FeatureGroup(name=f"{nombre} ({len(sub)})", show=(nombre.startswith("Resol")))
        for _, r in sub.iterrows():
            folium.CircleMarker(
                [r["lat"], r["lon"]], radius=6 if r["resolutiva"] else 3,
                color="#fff" if r["resolutiva"] else color, weight=1.5 if r["resolutiva"] else 0,
                fill=True, fill_color=color, fill_opacity=0.95 if r["resolutiva"] else 0.6,
                tooltip=f"{r['NOMBRE']} · {r['categoria_norm']} · {r['grupo_institucion']}"
            ).add_to(fg)
        fg.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.fit_bounds([[-18.4, -81.4], [-3.5, -70.0]])
    return m._repr_html_()


# ===========================================================================
# APP
# ===========================================================================
st.title("🏥 La hora dorada — acceso por carretera a establecimientos resolutivos")
st.caption("Tiempo de viaje en auto al establecimiento de categoría II-1 o superior más cercano. "
           "Piura (costa) · Ayacucho (sierra) · Ucayali (Amazonía). Fuente: RENIPRESS, SIGMED, "
           "OpenStreetMap, WorldPop.")

base = cargar_base()
matriz = cargar_matriz()
if base is None:
    st.warning("Faltan resultados de las Fases 2-3. Corre primero:\n\n"
               "```\npython src/routing.py --all\npython src/poblacion.py\npython src/metrics.py\n```")
    st.stop()
oferta = cargar_oferta()
dgeo = cargar_distritos_geojson()

# --- sidebar: filtros ---
st.sidebar.header("Filtros")
deps = sorted(base["departamento"].dropna().unique())
sel_dep = st.sidebar.multiselect("Departamento", deps, default=deps)
provs = sorted(base.loc[base["departamento"].isin(sel_dep), "nombre_provincia"].dropna().unique())
sel_prov = st.sidebar.multiselect("Provincia", provs, default=provs)
umbral = st.sidebar.slider("Umbral de tiempo (min)", 15, 180, UMBRAL_DEFECTO, 15)
cats_of = sorted(oferta["categoria_norm"].dropna().unique())
sel_cat = st.sidebar.multiselect("Categoría de establecimiento", cats_of, default=cats_of)
insts = sorted(oferta["grupo_institucion"].dropna().unique())
sel_inst = st.sidebar.multiselect("Institución", insts, default=insts)
capas_oferta = st.sidebar.multiselect(
    "Capas de establecimientos en el mapa",
    ["Resolutivos II-1+", "No resolutivos I-1 a I-4"], default=["Resolutivos II-1+"])

b = base[base["departamento"].isin(sel_dep) & base["nombre_provincia"].isin(sel_prov)].copy()
of = oferta[oferta["DEPARTAMENTO"].str.upper().isin(s.upper() for s in sel_dep)
            & oferta["categoria_norm"].isin(sel_cat) & oferta["grupo_institucion"].isin(sel_inst)].copy()
if b.empty:
    st.info("La selección no tiene centros poblados. Ajusta los filtros.")
    st.stop()

# --- KPIs ---
pob_tot = b["poblacion"].sum()
rut = b[b["t_min"].notna()]
pob_cub = b.loc[b["t_min"] <= umbral, "poblacion"].sum()
pob_lejos = b.loc[b["t_min"] > umbral, "poblacion"].sum()
pob_sin = pob_tot - rut["poblacion"].sum()
acc_dist = acceso_por_distrito(b)
peor = acc_dist.sort_values("acceso_min_ponderado", ascending=False, na_position="first").iloc[0]

k = st.columns(5)
k[0].metric("Población analizada", f"{pob_tot:,.0f}")
k[1].metric(f"Cubierta ≤ {umbral} min", f"{100*pob_cub/pob_tot:.1f}%", f"{pob_cub:,.0f} hab.")
k[2].metric(f"Más allá de {umbral} min", f"{100*pob_lejos/pob_tot:.1f}%", f"{pob_lejos:,.0f} hab.",
            delta_color="inverse")
k[3].metric("Mediana de acceso", f"{rut['t_min'].median():.0f} min" if len(rut) else "—")
k[4].metric("Peor distrito", str(peor["nombre_distrito"])[:18],
            "sin ruta" if pd.isna(peor["acceso_min_ponderado"]) else f"{peor['acceso_min_ponderado']:.0f} min",
            delta_color="inverse")
if pob_sin > 0:
    st.caption(f"⚠️ {pob_sin:,.0f} hab. ({100*pob_sin/pob_tot:.1f}%) en centros poblados sin ruta "
               "por carretera al conjunto resolutivo — marcados, no se les imputa distancia recta.")

tabs = st.tabs(["🗺️ Mapa", "📈 Distribución", "📋 Distritos", "🧪 Simulador", "🔍 Calidad de datos"])

# --- Mapa ---
with tabs[0]:
    if dgeo is None:
        st.info("No está `data/raw/limites_distrito.gpkg` — no se puede dibujar el coroplético.")
    else:
        st.components.v1.html(_mapa_coropletico(dgeo, acc_dist, of, capas_oferta), height=620)

# --- Distribución ---
with tabs[1]:
    st.subheader("Distribución del tiempo de acceso (ECDF ponderada por población)")
    corte = st.radio("Desagregar por", ["departamento", "urbano_rural"], horizontal=True)
    import altair as alt
    d = rut.copy()
    filas = []
    for val, g in d.groupby(corte, observed=True):
        g = g.sort_values("t_min")
        cw = np.cumsum(g["poblacion"].to_numpy()) / g["poblacion"].sum()
        filas.append(pd.DataFrame({"t_min": g["t_min"], "ecdf": cw, corte: val}))
    ecdf = pd.concat(filas)
    ch = (alt.Chart(ecdf).mark_line().encode(
        x=alt.X("t_min:Q", title="Tiempo de acceso (min)"),
        y=alt.Y("ecdf:Q", title="Proporción acumulada de población", axis=alt.Axis(format="%")),
        color=alt.Color(f"{corte}:N", title=corte.replace("_", " ").title()))
        .properties(height=380))
    regla = alt.Chart(pd.DataFrame({"x": [umbral]})).mark_rule(color="red", strokeDash=[4, 4]).encode(x="x")
    st.altair_chart(ch + regla, use_container_width=True)

    cob = bandas_cobertura(b, BANDAS, grupo="departamento")
    st.dataframe(cob.pivot_table(index="departamento", columns="banda", values="share_pct",
                                 observed=True).fillna(0).round(1), use_container_width=True)

# --- Distritos ---
with tabs[2]:
    st.subheader("Distritos ordenados por peor acceso ponderado por población")
    tabla = acc_dist.sort_values("acceso_min_ponderado", ascending=False, na_position="first")
    tabla = tabla[["nombre_distrito", "departamento", "provincia", "poblacion",
                   "acceso_min_ponderado", "pct_sin_ruta"]].reset_index(drop=True)
    st.dataframe(tabla, use_container_width=True, height=460)
    buf = io.StringIO(); tabla.to_csv(buf, index=False, encoding="utf-8-sig")
    st.download_button("⬇️ Descargar CSV", buf.getvalue(), "brechas_distritos.csv", "text/csv")

# --- Simulador ---
with tabs[3]:
    st.subheader("Simulador: subir establecimientos I-3 / I-4 a resolutivos")
    if matriz is None:
        st.info("Falta `data/outputs/matriz_tiempos.parquet` (Fase 2). El simulador la necesita.")
    else:
        candidatos = of[of["categoria_norm"].isin(["I-3", "I-4"])].copy()
        candidatos["etq"] = (candidatos["NOMBRE"] + " · " + candidatos["categoria_norm"]
                             + " · " + candidatos["DISTRITO"] + " (" + candidatos["cod_ipress"] + ")")
        elegidos_etq = st.multiselect(
            f"Establecimientos a 'ascender' ({len(candidatos)} candidatos I-3/I-4 en el filtro)",
            candidatos["etq"].tolist())
        elegidos = candidatos.loc[candidatos["etq"].isin(elegidos_etq), "cod_ipress"].tolist()

        cod_ccpp_sel = set(b["cod_ccpp"])
        m = matriz[matriz["cod_ccpp"].isin(cod_ccpp_sel)]
        res_ids = set(oferta.loc[oferta["resolutiva"], "cod_ipress"])
        t_base = (m[m["cod_ipress"].isin(res_ids)].groupby("cod_ccpp")["duration_min"].min())
        if elegidos:
            t_nuevo_cand = m[m["cod_ipress"].isin(elegidos)].groupby("cod_ccpp")["duration_min"].min()
            t_nuevo = pd.concat([t_base, t_nuevo_cand], axis=1).min(axis=1)
        else:
            t_nuevo = t_base

        sim = b[["cod_ccpp", "poblacion", "departamento"]].copy()
        sim["t_antes"] = sim["cod_ccpp"].map(t_base)
        sim["t_despues"] = sim["cod_ccpp"].map(t_nuevo)
        cub_antes = sim.loc[sim["t_antes"] <= umbral, "poblacion"].sum()
        cub_desp = sim.loc[sim["t_despues"] <= umbral, "poblacion"].sum()
        ganancia = cub_desp - cub_antes

        c = st.columns(3)
        c[0].metric(f"Cobertura ≤ {umbral} min — actual", f"{100*cub_antes/pob_tot:.1f}%")
        c[1].metric("Con el escenario", f"{100*cub_desp/pob_tot:.1f}%",
                    f"+{100*ganancia/pob_tot:.2f} pp")
        c[2].metric("Población que gana cobertura", f"{ganancia:,.0f} hab.")
        if elegidos:
            det = (sim.assign(gana=lambda x: (x["t_antes"] > umbral) & (x["t_despues"] <= umbral))
                   .groupby("departamento")["gana"].sum().rename("centros_poblados_que_ganan"))
            st.dataframe(det, use_container_width=True)
        st.caption("Recalculado con la matriz origen×instalación de la Fase 2 (perfil auto). "
                   "No se vuelve a rutear.")

# --- Calidad de datos ---
with tabs[4]:
    st.subheader("Informe de calidad de datos — Fase 1")
    q = cargar_calidad()
    if q is None:
        st.info("Falta `logs/quality_report.csv`.")
    else:
        st.dataframe(q, use_container_width=True, height=430)
        st.caption("Cada regla: cuántos registros marcó, qué se hizo y por qué. "
                   "Nada se elimina en silencio.")
