# -*- coding: utf-8 -*-
"""
Fase 4 — Tablero Streamlit "La hora dorada".

Lee SOLO archivos precalculados (Fases 1-3). No rutea ni construye grafos.

    python -m streamlit run app.py

Vistas: indicadores clave · mapa coroplético de acceso por distrito con capa
de establecimientos · distribución (curva de porcentaje acumulado) del tiempo
de acceso · tabla de distritos críticos con descarga · simulador de escenario
(elevar establecimientos I-3/I-4 a resolutivos y ver la ganancia de
cobertura) · informe de calidad de datos de la Fase 1.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
import yaml

# La lógica de métricas vive en src/metrics.py (el enunciado lo exige):
from src.metrics import bandas_cobertura

REPO_ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="La hora dorada", page_icon="🏥", layout="wide")

# --------------------------------------------------------------------------
# Paleta institucional y tipografía
# --------------------------------------------------------------------------
AZUL = "#1f3a5f"          # títulos / acento (tipografía y tema)
# --- colores del MAPA: se mantienen los originales (previos al rediseño) ---
GRANATE = "#d00000"       # establecimientos resolutivos + línea de umbral
GRIS = "#8d99ae"          # establecimientos no resolutivos
ORO = "#c99a2e"           # ubicación recuperada / aproximada (borde)
ESCALA_ACCESO = ["#1a9850", "#fee08b", "#d73027", "#7f0000"]   # acceso: verde=bueno -> granate=malo

st.markdown(f"""
<style>
  html, body, [class*="css"] {{ font-family: "Source Sans 3","Segoe UI",system-ui,sans-serif; }}
  h1, h2, h3, h4 {{ color: {AZUL}; font-weight: 700; letter-spacing: .2px; }}
  .stTabs [data-baseweb="tab"] {{ font-size: 1rem; font-weight: 600; }}
  [data-testid="stMetricValue"] {{ color: {AZUL}; }}
  [data-testid="stMetricLabel"] {{ font-weight: 600; }}
  section[data-testid="stSidebar"] h2 {{ font-size: 1.05rem; }}
  .stDataFrame thead tr th {{ background: #eef2f7; color: {AZUL}; font-weight: 700; }}
  hr {{ margin: .6rem 0 1rem; }}
</style>
""", unsafe_allow_html=True)


def _p(rel: str) -> Path:
    return REPO_ROOT / rel


# --------------------------------------------------------------------------
# Nombres legibles para las tablas (el viewer no debe ver snake_case)
# --------------------------------------------------------------------------
NOMBRES = {
    "ubigeo_distrito": "UBIGEO", "ubigeo_provincia": "UBIGEO prov.",
    "ubigeo_departamento": "UBIGEO dep.", "nombre_distrito": "Distrito",
    "nombre_provincia": "Provincia", "provincia": "Provincia", "departamento": "Departamento",
    "poblacion": "Población", "poblacion_ruteable": "Población con ruta",
    "acceso_min_ponderado": "Acceso medio (min)", "pct_sin_ruta": "% sin ruta",
    "n_centros_poblados": "Centros poblados", "rank": "N.º", "banda": "Banda de tiempo",
    "share_pct": "% de población", "poblacion_total_grupo": "Población del grupo",
    "t_min": "Acceso en auto (min)", "distance_km": "Distancia (km)",
    "duration_min": "Tiempo (min)", "urbano_rural": "Ámbito",
    "franja_altitud": "Franja altitudinal", "altitud_media_m": "Altitud media (m)",
    "cod_ccpp": "Cód. centro poblado", "cod_ipress": "Cód. establecimiento",
    "centros_poblados_que_ganan": "Centros poblados que ganan cobertura",
    "NOMBRE": "Establecimiento", "CATEGORIA": "Categoría (registro)",
    "categoria_norm": "Categoría", "grupo_institucion": "Institución",
    "DISTRITO": "Distrito", "PROVINCIA": "Provincia", "DEPARTAMENTO": "Departamento",
    "dataset": "Conjunto", "regla": "Regla de validación", "registros_evaluados": "Evaluados",
    "registros_marcados": "Marcados", "porcentaje": "%", "accion": "Acción", "motivo": "Motivo",
}
BANDA_ES = {"0-30": "≤ 30 min", "30-60": "30–60 min", "60-120": "60–120 min",
            ">120": "> 120 min", "sin_ruta": "Sin ruta"}


def _es(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns=lambda c: NOMBRES.get(c, str(c).replace("_", " ").capitalize()))


# --------------------------------------------------------------------------
@st.cache_data
def cargar_config() -> dict:
    with open(_p("config.md"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


CFG = cargar_config()
BANDAS = list(CFG.get("metrics", {}).get("bandas_minutos", [30, 60, 120]))
UMBRAL_DEFECTO = int(CFG["routing"].get("umbral_hora_oro_minutos", 60))


@st.cache_data
def cargar_base() -> pd.DataFrame | None:
    f = _p("data/outputs/metricas_por_centro_poblado.csv")
    if not f.exists():
        return None
    return pd.read_csv(f, dtype={"ubigeo_distrito": str, "ubigeo_provincia": str,
                                 "ubigeo_departamento": str, "cod_ccpp": str})


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
def cargar_oferta() -> pd.DataFrame:
    import geopandas as gpd
    g = gpd.read_file(_p("data/processed/renipress_nacional.gpkg"))
    nombres = {CFG["departamentos"]["costero"], CFG["departamentos"]["andino"],
               CFG["departamentos"]["amazonico"]}
    g = g[g["DEPARTAMENTO"].str.upper().isin(nombres)].copy()
    g["lon"], g["lat"] = g.geometry.x, g.geometry.y
    g["cod_ipress"] = g["COD_IPRESS"].astype(str)
    g["resolutiva"] = g["resolutiva"].astype("boolean").fillna(False).astype(bool)
    g["coordenada_recuperada"] = (g.get("coordenada_recuperada", pd.Series(False, index=g.index))
                                  .astype("boolean").fillna(False).astype(bool))
    inst = g.get("INSTITUCION", pd.Series("", index=g.index)).fillna("").str.upper()
    g["grupo_institucion"] = np.select(
        [inst.str.contains("MINSA|GOBIERNO REGIONAL|REGIÓN|GOB.REG", regex=True),
         inst.str.contains("ESSALUD|SEGURO SOCIAL", regex=True),
         inst.str.contains("PRIVAD", regex=True),
         inst.str.contains("SANIDAD|FUERZAS ARMADAS|PNP|POLIC", regex=True)],
        ["MINSA / Gob. Regional", "EsSalud", "Privado", "Sanidad FF. AA. / PNP"], default="Otro")
    return g[["cod_ipress", "NOMBRE", "DEPARTAMENTO", "PROVINCIA", "DISTRITO", "CATEGORIA",
              "categoria_norm", "grupo_institucion", "resolutiva", "coordenada_recuperada",
              "lon", "lat"]]


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


# --------------------------------------------------------------------------
def acceso_por_distrito(base: pd.DataFrame) -> pd.DataFrame:
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

    return (d.groupby(["ubigeo_distrito", "nombre_distrito"], observed=True)
            .apply(_agg, include_groups=False).reset_index())


def _mapa_coropletico(distritos_geojson: str, acc_dist: pd.DataFrame,
                      oferta: pd.DataFrame, capas_oferta: list[str]) -> str:
    import folium
    import branca

    acc = acc_dist.set_index("ubigeo_distrito")
    vals = acc["acceso_min_ponderado"].dropna()
    vmax = float(np.nanpercentile(vals, 95)) if len(vals) else 120.0
    cmap = branca.colormap.LinearColormap(
        ESCALA_ACCESO, vmin=0, vmax=vmax,
        caption="Tiempo medio de viaje en auto al establecimiento resolutivo más cercano "
                "(minutos, ponderado por población)")

    gj = json.loads(distritos_geojson)
    for feat in gj["features"]:
        ub = str(feat["properties"].get("ubigeo"))
        r = acc.loc[ub] if ub in acc.index else None
        feat["properties"]["acceso"] = (None if r is None or pd.isna(r["acceso_min_ponderado"])
                                        else float(r["acceso_min_ponderado"]))
        feat["properties"]["poblacion"] = 0 if r is None else int(r["poblacion"])
        feat["properties"]["sinruta"] = None if r is None else float(r["pct_sin_ruta"])

    m = folium.Map(location=[-9.5, -75.5], zoom_start=6, control_scale=True,
                   tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
                   attr="Esri — World Light Gray")

    def _style(feat):
        v = feat["properties"]["acceso"]
        return {"fillColor": "#d9d9d9" if v is None else cmap(v),
                "color": "#7a7a7a", "weight": 0.6, "fillOpacity": 0.78}

    folium.GeoJson(
        gj, name="Acceso por distrito", style_function=_style,
        highlight_function=lambda f: {"weight": 2, "color": "#1f3a5f"},
        tooltip=folium.GeoJsonTooltip(
            fields=["nombdist", "nombdep", "acceso", "poblacion", "sinruta"],
            aliases=["Distrito", "Departamento", "Acceso medio (min)", "Población", "% sin ruta"],
            localize=True)).add_to(m)
    cmap.add_to(m)

    grupos = {"Establecimientos resolutivos (II-1 o superior)": (GRANATE, oferta[oferta["resolutiva"]]),
              "Establecimientos no resolutivos (I-1 a I-4)": (GRIS, oferta[~oferta["resolutiva"]])}
    for nombre, (color, sub) in grupos.items():
        if nombre not in capas_oferta or not len(sub):
            continue
        fg = folium.FeatureGroup(name=f"{nombre} ({len(sub)})", show=nombre.startswith("Establecimientos res"))
        for _, r in sub.iterrows():
            recu = bool(r["coordenada_recuperada"])
            es_res = bool(r["resolutiva"])
            folium.CircleMarker(
                [r["lat"], r["lon"]], radius=6 if es_res else 3,
                color=ORO if recu else ("#ffffff" if es_res else color),
                weight=2 if (es_res or recu) else 0, fill=True, fill_color=color,
                fill_opacity=0.95 if es_res else 0.55,
                tooltip=(f"{r['NOMBRE']} · {r['categoria_norm']} · {r['grupo_institucion']}"
                         + (" · ubicación aproximada (coordenada recuperada)" if recu else ""))
            ).add_to(fg)
        fg.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.fit_bounds([[-18.4, -81.4], [-3.5, -70.0]])
    return m._repr_html_()


# ===========================================================================
# APP
# ===========================================================================
st.title("La hora dorada")
st.markdown("#### Tiempo de acceso por carretera a establecimientos de salud resolutivos")
st.caption("Minutos en auto al establecimiento de categoría II-1 o superior más cercano, sobre la "
           "red vial real. Departamentos: Piura (costa), Ayacucho (sierra) y Ucayali (Amazonía). "
           "Fuentes: RENIPRESS/SUSALUD, SIGMED/MINEDU, OpenStreetMap y WorldPop.")
st.divider()

base = cargar_base()
matriz = cargar_matriz()
if base is None:
    st.warning("Faltan resultados de las Fases 2-3. Ejecute primero:\n\n"
               "```\npython src/routing.py --all\npython src/poblacion.py\npython src/metrics.py\n```")
    st.stop()
oferta = cargar_oferta()
dgeo = cargar_distritos_geojson()

# --- barra lateral: filtros ---
st.sidebar.header("Filtros")
deps = sorted(base["departamento"].dropna().unique())
sel_dep = st.sidebar.multiselect("Departamento", deps, default=deps)
provs = sorted(base.loc[base["departamento"].isin(sel_dep), "nombre_provincia"].dropna().unique())
sel_prov = st.sidebar.multiselect("Provincia", provs, default=provs)
umbral = st.sidebar.slider("Umbral de tiempo (minutos)", 15, 180, UMBRAL_DEFECTO, 15)
cats_of = sorted(oferta["categoria_norm"].dropna().unique())
sel_cat = st.sidebar.multiselect("Categoría del establecimiento", cats_of, default=cats_of)
insts = sorted(oferta["grupo_institucion"].dropna().unique())
sel_inst = st.sidebar.multiselect("Institución", insts, default=insts)
capas_oferta = st.sidebar.multiselect(
    "Capas de establecimientos en el mapa",
    ["Establecimientos resolutivos (II-1 o superior)", "Establecimientos no resolutivos (I-1 a I-4)"],
    default=["Establecimientos resolutivos (II-1 o superior)"])

b = base[base["departamento"].isin(sel_dep) & base["nombre_provincia"].isin(sel_prov)].copy()
of = oferta[oferta["DEPARTAMENTO"].str.upper().isin(s.upper() for s in sel_dep)
            & oferta["categoria_norm"].isin(sel_cat)
            & oferta["grupo_institucion"].isin(sel_inst)].copy()
if b.empty:
    st.info("La selección no contiene centros poblados. Ajuste los filtros.")
    st.stop()

# --- indicadores clave ---
pob_tot = b["poblacion"].sum()
rut = b[b["t_min"].notna()]
pob_cub = b.loc[b["t_min"] <= umbral, "poblacion"].sum()
pob_lejos = b.loc[b["t_min"] > umbral, "poblacion"].sum()
pob_sin = pob_tot - rut["poblacion"].sum()
acc_dist = acceso_por_distrito(b)
peor = acc_dist.sort_values("acceso_min_ponderado", ascending=False, na_position="first").iloc[0]

k = st.columns(5)
k[0].metric("Población analizada", f"{pob_tot:,.0f}".replace(",", " "))
k[1].metric(f"Cubierta en ≤ {umbral} min", f"{100 * pob_cub / pob_tot:.1f} %",
            f"{pob_cub:,.0f} hab.".replace(",", " "))
k[2].metric(f"Más allá de {umbral} min", f"{100 * pob_lejos / pob_tot:.1f} %",
            f"{pob_lejos:,.0f} hab.".replace(",", " "), delta_color="inverse")
k[3].metric("Mediana de acceso", f"{rut['t_min'].median():.0f} min" if len(rut) else "—")
k[4].metric("Distrito más crítico", str(peor["nombre_distrito"]).title(),
            "sin ruta" if pd.isna(peor["acceso_min_ponderado"]) else f"{peor['acceso_min_ponderado']:.0f} min",
            delta_color="inverse")
if pob_sin > 0:
    st.caption(f"⚠ {pob_sin:,.0f} habitantes ({100 * pob_sin / pob_tot:.1f} %) en centros poblados sin "
               "ruta por carretera al conjunto resolutivo; se marcan como tales y no se les imputa "
               "distancia en línea recta.".replace(",", " "))
st.divider()

t_mapa, t_dist, t_crit, t_sim, t_cal = st.tabs(
    ["Mapa", "Distribución del acceso", "Distritos críticos", "Simulador de escenario",
     "Calidad de datos"])

# --- Mapa ---
with t_mapa:
    if dgeo is None:
        st.info("No se encuentra `data/raw/limites_distrito.gpkg`; no se puede dibujar el coroplético.")
    else:
        st.components.v1.html(_mapa_coropletico(dgeo, acc_dist, of, capas_oferta), height=640)
        st.caption("Distritos coloreados por el tiempo medio de acceso ponderado por población. "
                   "Borde dorado: establecimiento con coordenada recuperada (ubicación aproximada).")

# --- Distribución ---
with t_dist:
    st.subheader("Distribución del tiempo de acceso")
    st.caption("Curva de porcentaje acumulado de población (ponderada) según el tiempo de acceso en auto.")
    etq = {"departamento": "Departamento", "urbano_rural": "Ámbito (urbano/rural)"}
    corte = st.radio("Desagregar por", list(etq), format_func=lambda x: etq[x], horizontal=True)

    import altair as alt
    filas = []
    for val, g in rut.groupby(corte, observed=True):
        g = g.sort_values("t_min")
        cw = np.cumsum(g["poblacion"].to_numpy()) / g["poblacion"].sum()
        filas.append(pd.DataFrame({"tiempo": g["t_min"], "acum": cw, "grupo": str(val).title()}))
    ecdf = pd.concat(filas)
    linea = (alt.Chart(ecdf).mark_line(strokeWidth=2).encode(
        x=alt.X("tiempo:Q", title="Tiempo de acceso (minutos)"),
        y=alt.Y("acum:Q", title="Proporción acumulada de población", axis=alt.Axis(format="%")),
        color=alt.Color("grupo:N", title=etq[corte],
                        scale=alt.Scale(scheme="tableau10")))
        .properties(height=380))
    regla = (alt.Chart(pd.DataFrame({"x": [umbral]})).mark_rule(color=GRANATE, strokeDash=[5, 4])
             .encode(x="x", tooltip=alt.value(f"Umbral: {umbral} min")))
    st.altair_chart(linea + regla, use_container_width=True)

    st.markdown("**Cobertura de población por banda de tiempo, por departamento**")
    cob = bandas_cobertura(b, BANDAS, grupo="departamento")
    piv = (cob.pivot_table(index="departamento", columns="banda", values="share_pct", observed=True)
           .fillna(0).round(1))
    piv.columns = [BANDA_ES.get(c, c) for c in piv.columns]
    orden = [c for c in ["≤ 30 min", "30–60 min", "60–120 min", "> 120 min", "Sin ruta"] if c in piv.columns]
    piv = piv[orden]
    piv.index.name = "Departamento"
    st.dataframe(piv, use_container_width=True,
                 column_config={c: st.column_config.NumberColumn(c, format="%.1f %%") for c in piv.columns})

# --- Distritos críticos ---
with t_crit:
    st.subheader("Distritos ordenados por peor acceso ponderado por población")
    tabla = (acc_dist.sort_values("acceso_min_ponderado", ascending=False, na_position="first")
             [["nombre_distrito", "departamento", "provincia", "poblacion",
               "acceso_min_ponderado", "pct_sin_ruta"]].reset_index(drop=True))
    tabla["nombre_distrito"] = tabla["nombre_distrito"].str.title()
    tabla["provincia"] = tabla["provincia"].str.title()
    tabla.index = tabla.index + 1
    tabla.index.name = "N.º"
    st.dataframe(
        _es(tabla), use_container_width=True, height=460,
        column_config={
            "Población": st.column_config.NumberColumn("Población", format="%d"),
            "Acceso medio (min)": st.column_config.NumberColumn("Acceso medio (min)", format="%.1f"),
            "% sin ruta": st.column_config.NumberColumn("% sin ruta", format="%.1f %%"),
        })
    buf = io.StringIO()
    _es(tabla).to_csv(buf, encoding="utf-8-sig")
    st.download_button("Descargar tabla (CSV)", buf.getvalue(),
                       "distritos_criticos.csv", "text/csv")

# --- Simulador ---
with t_sim:
    st.subheader("Simulador de escenario: elevar establecimientos I-3 / I-4 a resolutivos")
    if matriz is None:
        st.info("Falta `data/outputs/matriz_tiempos.parquet` (Fase 2); el simulador la necesita.")
    else:
        candidatos = of[of["categoria_norm"].isin(["I-3", "I-4"])].copy()
        candidatos["etq"] = (candidatos["NOMBRE"].str.title() + "  —  " + candidatos["categoria_norm"]
                             + "  —  " + candidatos["DISTRITO"].str.title()
                             + "  (" + candidatos["cod_ipress"] + ")")
        elegidos_etq = st.multiselect(
            f"Establecimientos a elevar  ({len(candidatos)} candidatos I-3/I-4 en la selección actual)",
            candidatos["etq"].tolist())
        elegidos = candidatos.loc[candidatos["etq"].isin(elegidos_etq), "cod_ipress"].tolist()

        m = matriz[matriz["cod_ccpp"].isin(set(b["cod_ccpp"]))]
        res_ids = set(oferta.loc[oferta["resolutiva"], "cod_ipress"])
        t_base = m[m["cod_ipress"].isin(res_ids)].groupby("cod_ccpp")["duration_min"].min()
        if elegidos:
            t_cand = m[m["cod_ipress"].isin(elegidos)].groupby("cod_ccpp")["duration_min"].min()
            t_nuevo = pd.concat([t_base, t_cand], axis=1).min(axis=1)
        else:
            t_nuevo = t_base

        sim = b[["cod_ccpp", "poblacion", "departamento"]].copy()
        sim["t_antes"] = sim["cod_ccpp"].map(t_base)
        sim["t_despues"] = sim["cod_ccpp"].map(t_nuevo)
        cub_antes = sim.loc[sim["t_antes"] <= umbral, "poblacion"].sum()
        cub_desp = sim.loc[sim["t_despues"] <= umbral, "poblacion"].sum()
        ganancia = cub_desp - cub_antes

        c = st.columns(3)
        c[0].metric(f"Cobertura ≤ {umbral} min (actual)", f"{100 * cub_antes / pob_tot:.1f} %")
        c[1].metric("Cobertura con el escenario", f"{100 * cub_desp / pob_tot:.1f} %",
                    f"+{100 * ganancia / pob_tot:.2f} puntos porcentuales")
        c[2].metric("Población que gana cobertura", f"{ganancia:,.0f} hab.".replace(",", " "))
        if elegidos:
            det = (sim.assign(gana=lambda x: (x["t_antes"] > umbral) & (x["t_despues"] <= umbral))
                   .groupby("departamento")["gana"].sum()
                   .rename("centros_poblados_que_ganan").reset_index())
            st.dataframe(_es(det), use_container_width=True, hide_index=True)
        st.caption("Recalculado con la matriz origen × establecimiento de la Fase 2 (perfil auto). "
                   "No se vuelve a rutear: la respuesta es instantánea.")

# --- Calidad de datos ---
with t_cal:
    st.subheader("Informe de calidad de datos — Fase 1")
    q = cargar_calidad()
    if q is None:
        st.info("Falta `logs/quality_report.csv`.")
    else:
        st.dataframe(
            _es(q), use_container_width=True, height=470, hide_index=True,
            column_config={
                "Evaluados": st.column_config.NumberColumn("Evaluados", format="%d"),
                "Marcados": st.column_config.NumberColumn("Marcados", format="%d"),
                "%": st.column_config.NumberColumn("%", format="%.2f %%"),
            })
        st.caption("Para cada regla: cuántos registros marcó, qué se hizo con ellos y por qué. "
                   "Ningún registro se elimina en silencio; las coordenadas faltantes con UBIGEO "
                   "válido se recuperan al centroide de su distrito.")
