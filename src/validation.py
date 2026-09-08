"""Fase 1 — Limpieza y validación de datos.

Lee data/raw/renipress.csv (oferta) y data/raw/centros_poblados.geojson
(demanda), aplica las reglas de calidad exigidas por el enunciado, y
escribe:
  - data/processed/renipress_nacional.gpkg y centros_poblados_nacional.gpkg
  - data/processed/renipress_<departamento>.gpkg y centros_poblados_<departamento>.gpkg
    para cada uno de los 3 departamentos declarados en config.md
  - logs/reporte_calidad_datos.md (y .json) con cuántos registros marcó
    cada regla, qué se hizo con ellos y por qué.

Ninguna regla elimina filas en silencio: cada `ReporteCalidad.registrar()`
queda en el informe final, incluso cuando la acción es "no evaluado"
porque falta un insumo (p. ej. los límites administrativos).

Uso:
    python src/validation.py
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from config import (
    CONFIG,
    codigos_inei_seleccionados,
    departamentos_seleccionados,
    ensure_dirs,
    resolve_path,
)

try:
    import geopandas as gpd
    from shapely.geometry import Point
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "Este script requiere geopandas/shapely. Instala las dependencias con "
        "'pip install -r requirements.txt' antes de continuar."
    ) from e


# ---------------------------------------------------------------------------
# Esquema de columnas de RENIPRESS
# ---------------------------------------------------------------------------
# Nombres reales del CSV publicado por SUSALUD (verificado sobre el snapshot
# RENIPRESS_31-08-2026.csv). NORTE/ESTE son nombres heredados de un esquema
# UTM, pero en la práctica contienen latitud/longitud en grados decimales
# (NORTE=latitud, ESTE=longitud) — no coordenadas UTM en metros. Es
# exactamente el tipo de inconsistencia que el enunciado pide dejar visible
# y documentada en vez de escondida detrás de un nombre genérico "lat/lon".
RENIPRESS_COLUMNAS = {
    "id": "COD_IPRESS",
    "nombre": "NOMBRE",
    "categoria_raw": "CATEGORIA",
    "estado_raw": "ESTADO",
    "departamento": "DEPARTAMENTO",
    "provincia": "PROVINCIA",
    "distrito": "DISTRITO",
    "ubigeo": "UBIGEO",
    "lat": "NORTE",
    "lon": "ESTE",
}

COLUMNAS_TEXTO_RENIPRESS = [
    "NOMBRE", "DIRECCION", "INSTITUCION", "UNIDAD_EJECUTORA",
    "DEPARTAMENTO", "PROVINCIA", "DISTRITO",
]


# ---------------------------------------------------------------------------
# Informe de calidad
# ---------------------------------------------------------------------------

class ReporteCalidad:
    """Acumula, en orden, lo que cada regla de validación hizo."""

    def __init__(self, dataset: str):
        self.dataset = dataset
        self.reglas: list[dict[str, Any]] = []

    def registrar(self, regla: str, n_marcados: int, accion: str, motivo: str, n_total: int | None = None) -> None:
        pct = round(100 * n_marcados / n_total, 2) if n_total else None
        self.reglas.append({
            "regla": regla,
            "registros_marcados": int(n_marcados),
            "porcentaje": pct,
            "accion": accion,
            "motivo": motivo,
        })
        pct_txt = f" ({pct}%)" if pct is not None else ""
        print(f"  [{self.dataset}] {regla}: {n_marcados}{pct_txt} -> {accion}")

    def como_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "reglas": self.reglas,
        }


def escribir_reporte_md(reporte_dict: dict[str, Any], ruta: Path) -> None:
    lineas = [
        f"# Informe de calidad de datos — {reporte_dict['dataset']}",
        f"Generado: {reporte_dict['generado']}",
        "",
        "| Regla | Registros marcados | % | Acción | Motivo |",
        "|---|---|---|---|---|",
    ]
    for r in reporte_dict["reglas"]:
        pct = f"{r['porcentaje']}%" if r["porcentaje"] is not None else "—"
        motivo = r["motivo"].replace("|", "/")
        lineas.append(f"| {r['regla']} | {r['registros_marcados']} | {pct} | {r['accion']} | {motivo} |")
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Regla: codificación de texto (UTF-8 vs Latin-1)
# ---------------------------------------------------------------------------
# Mojibake conocido: verificado en RENIPRESS 2026-08, donde 'ñ' se exporta
# corrupta como 'ÿ' (y 'Ñ' como 'Ÿ'), un bug de transcodificación aguas
# arriba (probable Windows-1252 -> UTF-8 mal aplicado sobre los bytes 0xF1
# / 0xD1). Se corrige por reemplazo directo, no por reintento de decode,
# porque el archivo en sí decodifica como UTF-8 válido.
_MOJIBAKE_DIRECTO = {"ÿ": "ñ", "Ÿ": "Ñ"}
# Señal de doble codificación (UTF-8 leído como Latin-1, p. ej. 'Ã±' en vez
# de 'ñ'): si aparece, se intenta revertir con encode('latin-1').decode('utf-8').
_MARCAS_DOBLE_CODIFICACION = ("Ã", "Â", "�")


def corregir_mojibake(valor: Any) -> tuple[Any, bool]:
    if not isinstance(valor, str) or not valor:
        return valor, False
    corregido = valor
    cambiado = False
    for malo, bueno in _MOJIBAKE_DIRECTO.items():
        if malo in corregido:
            corregido = corregido.replace(malo, bueno)
            cambiado = True
    if any(marca in corregido for marca in _MARCAS_DOBLE_CODIFICACION):
        try:
            corregido = corregido.encode("latin-1").decode("utf-8")
            cambiado = True
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
    return corregido, cambiado


def corregir_codificacion_columnas(df, columnas: list[str], reporte: ReporteCalidad):
    total_corregidas = 0
    for col in columnas:
        if col not in df.columns:
            continue
        resultados = df[col].map(corregir_mojibake)
        df[col] = resultados.map(lambda r: r[0])
        total_corregidas += sum(1 for r in resultados if r[1])
    reporte.registrar(
        "codificacion_texto_utf8_vs_latin1",
        total_corregidas,
        "corregido" if total_corregidas else "sin problemas detectados",
        "reemplazo de mojibake conocido (ÿ->ñ, Ÿ->Ñ) y reversión de doble codificación UTF-8/Latin-1 en campos de texto",
    )
    return df


# ---------------------------------------------------------------------------
# Regla: normalización de CATEGORIA (no está limpia en el CSV crudo)
# ---------------------------------------------------------------------------
# Reglas de normalización (documentadas aquí porque RENIPRESS no las
# documenta en su diccionario de datos):
#  1. Se ignoran mayúsculas/minúsculas y espacios.
#  2. Vacío, nulo o "0" -> categoría desconocida (RENIPRESS usa "0" como
#     centinela de "sin categorizar"; ~24% de los registros en el snapshot
#     de 2026-08).
#  3. Cualquier cadena que calce con romano (I..III) + sufijo (1,2,3,E),
#     con o sin guion, se reescribe a la forma canónica "II-1".
#  4. Cualquier otra cadena -> categoría desconocida (se conserva el dato
#     crudo, pero no participa del cálculo de oferta resolutiva).
_PATRON_CATEGORIA = re.compile(r"^(I{1,3})-?(1|2|3|E)$")
_CATEGORIA_VACIA = {"", "0", "NAN", "NONE", "S/C", "SINCATEGORIA", "SINCATEGORÍA"}


def normalizar_categoria(raw: Any) -> str | None:
    if raw is None:
        return None
    txt = re.sub(r"\s+", "", str(raw).strip().upper())
    if txt in _CATEGORIA_VACIA:
        return None
    m = _PATRON_CATEGORIA.match(txt)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}"


def clasificar_categoria(categoria_normalizada: str | None) -> str:
    resolutivas = set(CONFIG["categorias"]["resolutivas"])
    no_concluyentes = set(CONFIG["categorias"]["no_concluyentes"])
    if categoria_normalizada in resolutivas:
        return "resolutiva"
    if categoria_normalizada in no_concluyentes:
        return "no_concluyente"
    return "desconocida"


def normalizar_estado(raw: Any) -> str:
    return str(raw).strip().upper()


def es_estado_activo(estado_normalizado: str) -> bool:
    return estado_normalizado in {v.upper() for v in CONFIG["estado_operativo_valido"]}


# ---------------------------------------------------------------------------
# Regla: coordenadas (faltantes, cero, intercambiadas, fuera de Perú)
# ---------------------------------------------------------------------------

def limpiar_coordenadas(df, lat_col: str, lon_col: str, reporte: ReporteCalidad):
    bbox = CONFIG["validacion"]["bbox_peru"]
    tol = CONFIG["validacion"]["tolerancia_coordenada_cero"]
    n_total = len(df)

    lat = pd.to_numeric(df[lat_col], errors="coerce")
    lon = pd.to_numeric(df[lon_col], errors="coerce")

    # Regla 1 — faltantes/nulas/no numéricas: no se puede ubicar el punto
    # ni calcular distancia; no es recuperable sin geocodificación externa.
    faltante = lat.isna() | lon.isna()
    reporte.registrar(
        "coordenadas_faltantes_o_nulas", int(faltante.sum()), "eliminado",
        "sin coordenada numérica no se puede calcular la distancia por carretera (Fase 2)",
        n_total,
    )
    df, lat, lon = df.loc[~faltante].copy(), lat.loc[~faltante], lon.loc[~faltante]

    # Regla 2 — coordenadas efectivamente cero: centinela de dato faltante,
    # no una posición real cerca de (0,0). Incluye ruido de punto flotante
    # cercano a cero (p. ej. -7.8e-07) observado en el snapshot real.
    cero = lat.abs().lt(tol) | lon.abs().lt(tol)
    reporte.registrar(
        "coordenadas_cero", int(cero.sum()), "eliminado",
        f"|valor| < {tol} se trata como centinela de nulo, no como coordenada real",
        n_total,
    )
    df, lat, lon = df.loc[~cero].copy(), lat.loc[~cero], lon.loc[~cero]

    # Regla 3 — latitud/longitud intercambiadas: si el par intercambiado
    # cae dentro del bbox de Perú y el original no, se corrige (no se
    # elimina) y queda documentada la tasa de recuperación.
    dentro_actual = lat.between(bbox["lat_min"], bbox["lat_max"]) & lon.between(bbox["lon_min"], bbox["lon_max"])
    dentro_si_swap = lon.between(bbox["lat_min"], bbox["lat_max"]) & lat.between(bbox["lon_min"], bbox["lon_max"])
    intercambiada = (~dentro_actual) & dentro_si_swap
    reporte.registrar(
        "coordenadas_lat_lon_intercambiadas", int(intercambiada.sum()), "corregido",
        "el par (lat,lon) cae fuera de Perú pero (lon,lat) cae dentro: se intercambian los valores",
        n_total,
    )
    lat_final = lat.mask(intercambiada, lon)
    lon_final = lon.mask(intercambiada, lat)
    df[lat_col] = lat_final
    df[lon_col] = lon_final

    # Regla 4 — fuera del bbox de Perú, tras corregir los intercambios.
    fuera_bbox = ~(
        lat_final.between(bbox["lat_min"], bbox["lat_max"])
        & lon_final.between(bbox["lon_min"], bbox["lon_max"])
    )
    reporte.registrar(
        "coordenadas_fuera_de_peru", int(fuera_bbox.sum()), "eliminado",
        f"fuera de lon[{bbox['lon_min']},{bbox['lon_max']}] x lat[{bbox['lat_min']},{bbox['lat_max']}] "
        "y no se explica por un intercambio lat/lon",
        n_total,
    )
    return df.loc[~fuera_bbox].copy()


def eliminar_duplicados(df, id_col: str, reporte: ReporteCalidad):
    n_total = len(df)
    dup = df[id_col].duplicated(keep="first")
    reporte.registrar(
        "codigos_duplicados", int(dup.sum()), "eliminado (se conserva la primera aparición)",
        f"'{id_col}' debe identificar un único establecimiento/centro poblado",
        n_total,
    )
    return df.loc[~dup].copy()


def _detectar_columna(columnas, candidatos: list[str]) -> str | None:
    normalizadas = {re.sub(r"[^A-Z0-9]", "", str(c).upper()): c for c in columnas}
    for cand in candidatos:
        clave = re.sub(r"[^A-Z0-9]", "", cand.upper())
        if clave in normalizadas:
            return normalizadas[clave]
    return None


def marcar_fuera_de_poligono(gdf_puntos, gdf_distritos, ubigeo_col_puntos: str, ubigeo_col_poligonos: str,
                              reporte: ReporteCalidad, ruta_detalle: Path):
    """Los puntos fuera del polígono de su propio distrito NO se eliminan
    (el polígono puede ser el impreciso, no el punto): quedan marcados con
    su propio registro en ruta_detalle, tal como pide el enunciado."""
    gdf_puntos = gdf_puntos.reset_index(drop=True)
    distritos = gdf_distritos[[ubigeo_col_poligonos, "geometry"]].rename(
        columns={ubigeo_col_poligonos: "_ubigeo_poligono"}
    )
    unido = gpd.sjoin(gdf_puntos, distritos, how="left", predicate="within")
    unido = unido[~unido.index.duplicated(keep="first")]
    unido = unido.reindex(gdf_puntos.index)

    ubigeo_punto = gdf_puntos[ubigeo_col_puntos].astype(str)
    ubigeo_poligono = unido["_ubigeo_poligono"].astype(str)
    fuera = unido["_ubigeo_poligono"].isna() | (ubigeo_poligono != ubigeo_punto)

    gdf_puntos = gdf_puntos.copy()
    gdf_puntos["fuera_de_poligono_distrital"] = fuera.to_numpy()

    reporte.registrar(
        "puntos_fuera_de_su_poligono_distrital", int(fuera.sum()), "conservado con advertencia",
        "el punto no cae dentro del polígono del distrito que declara su propio UBIGEO",
        len(gdf_puntos),
    )
    if fuera.any():
        cols = [c for c in [ubigeo_col_puntos, "NOMBRE", "DEPARTAMENTO", "DISTRITO"] if c in gdf_puntos.columns]
        gdf_puntos.loc[fuera, cols].to_csv(ruta_detalle, index=False, encoding="utf-8-sig")
        print(f"    detalle -> {ruta_detalle}")
    return gdf_puntos


# ---------------------------------------------------------------------------
# Carga de cada fuente
# ---------------------------------------------------------------------------

def _leer_csv_detectando_encoding(ruta: Path, sep: str, encoding_esperado: str, reporte: ReporteCalidad):
    """Intenta primero el encoding declarado en config.md (utf-8-sig). Si el
    archivo no decodifica así -- p. ej. un snapshot mensual futuro exportado
    en Latin-1 -- usa chardet sobre una muestra de bytes crudos y reintenta,
    dejando registrado en el informe de calidad qué encoding se usó."""
    try:
        df = pd.read_csv(ruta, sep=sep, encoding=encoding_esperado, dtype=str, low_memory=False)
        reporte.registrar(
            "codificacion_archivo", 0, f"leído como {encoding_esperado}",
            "el archivo decodifica correctamente con el encoding declarado en config.md",
        )
        return df
    except UnicodeDecodeError:
        import chardet

        deteccion = chardet.detect(ruta.read_bytes()[:200_000])
        encoding_detectado = deteccion["encoding"] or "latin-1"
        reporte.registrar(
            "codificacion_archivo", 1, f"leído como {encoding_detectado} (detectado por chardet)",
            f"{ruta.name} no decodifica como {encoding_esperado} "
            f"(confianza chardet: {deteccion['confidence']:.2f})",
        )
        return pd.read_csv(ruta, sep=sep, encoding=encoding_detectado, dtype=str, low_memory=False)


def cargar_renipress(reporte: ReporteCalidad):
    cfg = CONFIG["fuentes"]["renipress"]
    ruta = resolve_path(cfg["archivo_local"])
    if not ruta.exists():
        raise FileNotFoundError(f"No se encontró {ruta}. Corre primero 'python src/acquisition.py'.")

    df = _leer_csv_detectando_encoding(ruta, cfg["separador"], cfg["encoding"], reporte)
    n_original = len(df)

    df = corregir_codificacion_columnas(df, COLUMNAS_TEXTO_RENIPRESS, reporte)

    id_col = RENIPRESS_COLUMNAS["id"]
    df = limpiar_coordenadas(df, RENIPRESS_COLUMNAS["lat"], RENIPRESS_COLUMNAS["lon"], reporte)
    df = eliminar_duplicados(df, id_col, reporte)

    df["categoria_normalizada"] = df[RENIPRESS_COLUMNAS["categoria_raw"]].map(normalizar_categoria)
    df["categoria_grupo"] = df["categoria_normalizada"].map(clasificar_categoria)
    reporte.registrar(
        "categoria_no_reconocida", int((df["categoria_grupo"] == "desconocida").sum()),
        "conservado con advertencia (excluido del cálculo de más cercano)",
        "CATEGORIA vacía, '0' o que no calza con el patrón romano-sufijo",
        len(df),
    )

    df["estado_normalizado"] = df[RENIPRESS_COLUMNAS["estado_raw"]].map(normalizar_estado)
    df["operativo_activo"] = df["estado_normalizado"].map(es_estado_activo)
    # Resolutivo si y solo si está activo Y su categoría está en la lista
    # blanca de config.md -- ver definiciones del enunciado.
    df["resolutiva"] = df["operativo_activo"] & (df["categoria_grupo"] == "resolutiva")

    lon = pd.to_numeric(df[RENIPRESS_COLUMNAS["lon"]])
    lat = pd.to_numeric(df[RENIPRESS_COLUMNAS["lat"]])
    gdf = gpd.GeoDataFrame(df, geometry=[Point(xy) for xy in zip(lon, lat)], crs="EPSG:4326")

    n_final = len(gdf)
    reporte.registrar(
        "resumen_renipress", n_original - n_final, "eliminado (acumulado de las reglas anteriores)",
        f"{n_final} de {n_original} registros conservados ({100 * n_final / n_original:.1f}%)",
        n_original,
    )
    return gdf


def cargar_centros_poblados(reporte: ReporteCalidad):
    cfg = CONFIG["fuentes"]["sigmed"]
    ruta = resolve_path(cfg["archivo_local"])
    if not ruta.exists():
        print(f"  [centros_poblados] no se encontró {ruta}.")
        reporte.registrar(
            "archivo_no_disponible", 0, "omitido",
            f"no existe {ruta}; SIGMED requiere descarga manual desde {cfg['url']} (ver acquisition.py)",
            0,
        )
        return None

    gdf = gpd.read_file(ruta)
    n_original = len(gdf)
    gdf = gdf.set_crs("EPSG:4326") if gdf.crs is None else gdf.to_crs("EPSG:4326")

    invalida = gdf.geometry.isna() | gdf.geometry.is_empty
    reporte.registrar(
        "geometria_faltante_o_vacia", int(invalida.sum()), "eliminado",
        "sin geometría no se puede calcular distancia por carretera",
        n_original,
    )
    gdf = gdf.loc[~invalida].copy()

    columnas_texto = [c for c in gdf.columns if gdf[c].dtype == object and c != "geometry"]
    gdf = corregir_codificacion_columnas(gdf, columnas_texto, reporte)

    bbox = CONFIG["validacion"]["bbox_peru"]
    punto = gdf.geometry.representative_point()
    dentro = punto.y.between(bbox["lat_min"], bbox["lat_max"]) & punto.x.between(bbox["lon_min"], bbox["lon_max"])
    reporte.registrar(
        "coordenadas_fuera_de_peru", int((~dentro).sum()), "eliminado",
        "el punto representativo del centro poblado cae fuera del bbox de Perú",
        len(gdf),
    )
    gdf = gdf.loc[dentro].copy()

    id_col = _detectar_columna(gdf.columns, ["CODCP", "CCPP", "COD_CCPP", "CODIGO", "ID", "IDCCPP", "IDPROCPPOB"])
    if id_col:
        gdf = eliminar_duplicados(gdf, id_col, reporte)
    else:
        reporte.registrar(
            "codigos_duplicados", 0, "no evaluado",
            "no se encontró una columna de identificador único de centro poblado en el archivo de SIGMED",
            len(gdf),
        )
    return gdf


def cargar_limites_administrativos():
    """Para la Fase 1 solo se necesita el nivel distrito (el chequeo de
    punto-dentro-de-su-polígono usa UBIGEO a nivel distrital); las capas
    de provincia/departamento quedan en data/raw/ para reportes/mapas de
    fases posteriores."""
    capas = CONFIG["fuentes"]["limites_administrativos"]["capas"]
    ruta = resolve_path(capas["distrito"]["archivo_local"])
    if not ruta.exists():
        return None
    gdf = gpd.read_file(ruta)
    return gdf.set_crs("EPSG:4326") if gdf.crs is None else gdf.to_crs("EPSG:4326")


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def main() -> int:
    ensure_dirs()
    ruta_reportes = resolve_path(CONFIG["rutas"]["reports"])
    ruta_processed = resolve_path(CONFIG["rutas"]["processed"])
    ruta_reportes.mkdir(parents=True, exist_ok=True)
    ruta_processed.mkdir(parents=True, exist_ok=True)

    reporte = ReporteCalidad("renipress_y_centros_poblados")

    print("== RENIPRESS (oferta) ==")
    salud = cargar_renipress(reporte)

    print("\n== Límites administrativos ==")
    distritos = cargar_limites_administrativos()
    ubigeo_col_poligonos = None
    if distritos is not None:
        ubigeo_col_poligonos = _detectar_columna(distritos.columns, ["UBIGEO", "IDDIST", "UBIGEO_DIST", "COD_DIST"])
    if distritos is not None and ubigeo_col_poligonos:
        salud = marcar_fuera_de_poligono(
            salud, distritos, RENIPRESS_COLUMNAS["ubigeo"], ubigeo_col_poligonos,
            reporte, ruta_reportes / "renipress_fuera_de_poligono.csv",
        )
    else:
        reporte.registrar(
            "puntos_fuera_de_su_poligono_distrital", 0, "no evaluado",
            "no hay límites administrativos en caché (data/raw/) o no se identificó su columna UBIGEO; "
            "descárgalos manualmente (ver config.md -> fuentes.limites_administrativos) y vuelve a correr este script",
            len(salud),
        )

    print("\n== Centros poblados (demanda) ==")
    centros = cargar_centros_poblados(reporte)
    if centros is not None and distritos is not None and ubigeo_col_poligonos:
        ubigeo_col_centros = _detectar_columna(centros.columns, ["UBIGEO", "COD_UBIGEO"])
        if ubigeo_col_centros:
            centros = marcar_fuera_de_poligono(
                centros, distritos, ubigeo_col_centros, ubigeo_col_poligonos,
                reporte, ruta_reportes / "centros_poblados_fuera_de_poligono.csv",
            )

    salud_out = ruta_processed / "renipress_nacional.gpkg"
    salud.to_file(salud_out, driver="GPKG")
    print(f"\nGuardado: {salud_out} ({len(salud)} registros)")

    if centros is not None:
        centros_out = ruta_processed / "centros_poblados_nacional.gpkg"
        centros.to_file(centros_out, driver="GPKG")
        print(f"Guardado: {centros_out} ({len(centros)} registros)")

    print("\n== Subconjuntos por departamento (config.md) ==")
    codigos = codigos_inei_seleccionados()
    ubigeo_col_centros = _detectar_columna(centros.columns, ["UBIGEO", "COD_UBIGEO"]) if centros is not None else None
    for depto in departamentos_seleccionados().values():
        sub_salud = salud[salud[RENIPRESS_COLUMNAS["departamento"]].str.upper() == depto].copy()
        ruta_dep = ruta_processed / f"renipress_{depto.lower()}.gpkg"
        sub_salud.to_file(ruta_dep, driver="GPKG")
        n_resolutivos = int(sub_salud["resolutiva"].sum())
        print(f"  {depto}: {len(sub_salud)} establecimientos ({n_resolutivos} resolutivos) -> {ruta_dep}")

        if centros is not None and ubigeo_col_centros:
            codigo = codigos[depto]
            sub_centros = centros[centros[ubigeo_col_centros].astype(str).str.startswith(codigo)].copy()
            ruta_dep_c = ruta_processed / f"centros_poblados_{depto.lower()}.gpkg"
            sub_centros.to_file(ruta_dep_c, driver="GPKG")
            print(f"  {depto}: {len(sub_centros)} centros poblados -> {ruta_dep_c}")

    reporte_dict = reporte.como_dict()
    (ruta_reportes / "reporte_calidad_datos.json").write_text(
        json.dumps(reporte_dict, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    escribir_reporte_md(reporte_dict, ruta_reportes / "reporte_calidad_datos.md")
    print(f"\nInforme de calidad de datos -> {ruta_reportes / 'reporte_calidad_datos.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
