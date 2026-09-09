# -*- coding: utf-8 -*-
"""
Fase 1 (Paso 5) — Mapas interactivos Folium, uno por departamento.

Estilo tomado de `referencias/Folium_Mapas_Interactivos_Peru.ipynb`: fondo
claro (Esri Light Gray, sin API key), `MarkerCluster` para agrupar muchos
puntos, ficha HTML (`branca.IFrame`) en cada establecimiento resolutivo,
`LayerControl` para alternar capas, y leyenda/título embebidos.

Capas: límites distritales · centros poblados (demanda) · no resolutivos
I-1 a I-4 (oculta) · resolutivos II-1+ (con ficha).

    python src/mapas.py    # regenera los 3 HTML desde data/processed/
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.acquisition import (REPO_ROOT, cargar_config, codigos_inei,  # noqa: E402
                             departamentos_objetivo, ruta)

COLOR_RES = "#d00000"     # rojo  — II-1+
COLOR_NORES = "#8d99ae"   # gris  — I-1 a I-4 / desconocida
COLOR_DEM = "#2a9d8f"     # verde — centros poblados
COLOR_LIM = "#1d3557"     # azul  — borde de distritos
TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
TILES_ATTR = "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ"


def _v(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return str(x).replace("`", "'")           # el backtick rompe los tooltips de Folium


def _popup(row):
    import folium as fm
    import branca
    et, vl = "#1d3557", "#eef2f7"
    campos = [("Categoría", row.get("categoria_norm")), ("Estado", row.get("ESTADO")),
              ("Institución", row.get("INSTITUCION")), ("Provincia", row.get("PROVINCIA")),
              ("Distrito", row.get("DISTRITO")), ("Dirección", row.get("DIRECCION"))]
    trs = "".join(
        f'<tr><td style="background:{et};color:#fff;padding:5px 8px;font-size:11px">{k}</td>'
        f'<td style="background:{vl};padding:5px 8px;font-size:11px">{_v(v)}</td></tr>'
        for k, v in campos)
    html = ('<table style="width:320px;border-collapse:collapse;font-family:Arial,sans-serif">'
            f'<tr><td colspan="2" style="background:#03071e;color:#fff;font-weight:bold;font-size:12px;'
            f'padding:8px;text-align:center">&#127973; {_v(row.get("NOMBRE"))}</td></tr>{trs}</table>')
    return fm.Popup(branca.element.IFrame(html=html, width=350, height=230), parse_html=True)


def _banner(dep):
    return ('<div style="position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:9999;'
            'background:#03071e;color:#fff;padding:6px 18px;border-radius:6px;font-weight:bold;'
            f'font-family:Arial,sans-serif;font-size:14px">La hora dorada &middot; {dep.title()}</div>')


def _leyenda(dep, nres, nnores, ncp):
    f = '<div><span style="color:{c};font-size:15px">&#9679;</span>&nbsp;{t}</div>'
    return ('<div style="position:fixed;bottom:24px;left:24px;z-index:9999;background:#fff;padding:11px 13px;'
            'border-radius:8px;box-shadow:0 1px 6px rgba(0,0,0,.3);font-family:Arial,sans-serif;font-size:12px;'
            f'line-height:1.7"><div style="font-weight:bold;margin-bottom:3px">{dep.title()}</div>'
            + f.format(c=COLOR_RES, t=f"Resolutivo II-1+ ({nres})")
            + f.format(c=COLOR_NORES, t=f"No resolutivo I-1 a I-4 ({nnores})")
            + f.format(c=COLOR_DEM, t=f"Centros poblados ({ncp:,})")
            + '<div style="margin-top:4px;color:#666">RENIPRESS/SUSALUD &middot; SIGMED/INEI</div></div>')


def mapa_departamento(dep, codigo, oferta_ambito, demanda_ambito, distritos, ub_cp):
    import folium as fm
    from folium.plugins import MarkerCluster, FastMarkerCluster

    o = oferta_ambito[oferta_ambito["DEPARTAMENTO"].str.upper() == dep.upper()]
    res, nores = o[o["resolutiva"].astype(bool)], o[~o["resolutiva"].astype(bool)]
    d = (demanda_ambito[demanda_ambito[ub_cp].astype(str).str.zfill(6).str.startswith(str(codigo))]
         if ub_cp else demanda_ambito.iloc[0:0])
    lim = (distritos[distritos["ccdd"].astype(str).str.zfill(2) == str(codigo)]
           if distritos is not None and "ccdd" in distritos.columns else None)

    marco = lim if (lim is not None and len(lim)) else o
    minx, miny, maxx, maxy = marco.total_bounds
    ancho = max(maxx - minx, maxy - miny)
    zoom = 6 if ancho > 4 else 7 if ancho > 2 else 8
    # centro + zoom fijos: fit_bounds corre antes de que el contenedor tenga
    # tamaño y Leaflet calcula zoom 0 (mapa del tamaño del mundo).
    m = fm.Map(location=[(miny + maxy) / 2, (minx + maxx) / 2],
               tiles=TILES, attr=TILES_ATTR, zoom_start=zoom, control_scale=True)

    if lim is not None and len(lim):
        lim_dib = lim[["nombdist", "nombprov", "geometry"]].copy()
        lim_dib["geometry"] = lim_dib.geometry.simplify(0.003, preserve_topology=True)
        fm.GeoJson(lim_dib.to_json(), name="Límites distritales",
                   style_function=lambda _f: {"color": COLOR_LIM, "weight": 1, "fillOpacity": 0.03},
                   highlight_function=lambda _f: {"weight": 2.5, "fillOpacity": 0.12},
                   tooltip=fm.GeoJsonTooltip(fields=["nombdist", "nombprov"],
                                             aliases=["Distrito", "Provincia"],
                                             style="background:#fff;color:#333;font-family:Arial;font-size:12px;padding:8px")
                   ).add_to(m)

    if len(d):
        coords = [[round(g.y, 5), round(g.x, 5)] for g in d.geometry]
        fg = fm.FeatureGroup(name=f"Centros poblados — demanda ({len(d):,})", show=True)
        FastMarkerCluster(coords).add_to(fg)
        fg.add_to(m)

    fg_nr = fm.FeatureGroup(name=f"No resolutivos I-1 a I-4 ({len(nores)})", show=False)
    mc = MarkerCluster().add_to(fg_nr)
    for g, nom, cat in zip(nores.geometry, nores["NOMBRE"], nores["categoria_norm"]):
        fm.CircleMarker([g.y, g.x], radius=3, weight=0, fill=True, fill_color=COLOR_NORES,
                        fill_opacity=0.6,
                        tooltip=f"{_v(nom)} · {_v(cat) if cat else 's/categoría'}").add_to(mc)
    fg_nr.add_to(m)

    fg_r = fm.FeatureGroup(name=f"Resolutivos II-1+ ({len(res)})", show=True)
    for _, row in res.iterrows():
        fm.CircleMarker([row.geometry.y, row.geometry.x], radius=8, color="#fff", weight=2, fill=True,
                        fill_color=COLOR_RES, fill_opacity=0.95,
                        tooltip=f"★ {_v(row['NOMBRE'])} ({_v(row['categoria_norm'])})",
                        popup=_popup(row)).add_to(fg_r)
    fg_r.add_to(m)

    fm.LayerControl(collapsed=False).add_to(m)
    m.get_root().html.add_child(fm.Element(_banner(dep)))
    m.get_root().html.add_child(fm.Element(_leyenda(dep, len(res), len(nores), len(d))))
    return m


def generar_mapas_departamentos(oferta_ambito, demanda_ambito, distritos, ub_cp, cfg) -> dict:
    out_dir = ruta(cfg["rutas"]["outputs"])
    out_dir.mkdir(parents=True, exist_ok=True)
    codigos = codigos_inei(cfg)
    mapas = {}
    for rol, dep in departamentos_objetivo(cfg).items():
        m = mapa_departamento(dep, codigos[dep], oferta_ambito, demanda_ambito, distritos, ub_cp)
        destino = out_dir / f"mapa_acceso_{dep.lower()}.html"
        m.save(str(destino))
        mapas[dep] = m
        try:
            rel = destino.relative_to(REPO_ROOT)
        except ValueError:
            rel = destino
        print(f"  {rol:>9} · {dep:<9} -> {rel}")
    return mapas


def main(argv=None) -> int:
    import geopandas as gpd
    from src.validation import filtrar_ambito
    ap = argparse.ArgumentParser(description="Fase 1 (Paso 5) — mapas Folium por departamento")
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    cfg = cargar_config(a.config)
    proc = ruta(cfg["rutas"]["processed"])
    oferta = gpd.read_file(proc / "renipress_nacional.gpkg")
    demanda = gpd.read_file(proc / "centros_poblados_nacional.gpkg")
    ruta_dist = ruta(cfg["fuentes"]["limites_administrativos"]["capas"]["distrito"]["archivo_local"])
    distritos = gpd.read_file(ruta_dist) if ruta_dist.exists() else None
    oferta_ambito, demanda_ambito, ub_cp = filtrar_ambito(oferta, demanda, cfg)
    generar_mapas_departamentos(oferta_ambito, demanda_ambito, distritos, ub_cp, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
