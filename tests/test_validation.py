# -*- coding: utf-8 -*-
"""Pruebas de las funciones puras de la Fase 1 (validación). Sin archivos ni red.

    pytest -q tests/test_validation.py
"""
import numpy as np
import pandas as pd
import pytest

from src import validation as V

BBOX = {"lon_min": -81.4, "lon_max": -68.6, "lat_min": -18.4, "lat_max": -0.04}


# --- estandarización de texto -----------------------------------------
def test_normalizar_categoria():
    assert V.normalizar_categoria("  ii - 1 ") == "II-1"
    assert V.normalizar_categoria("III1") == "III-1"
    assert V.normalizar_categoria("I-2") == "I-2"
    assert V.normalizar_categoria("I-4") == "I-4"        # I-4 existe (bug: el patrón no la aceptaba)
    assert V.normalizar_categoria("I 4") == "I-4"
    assert V.normalizar_categoria("II-E") == "II-E"
    assert V.normalizar_categoria("0") is None
    assert V.normalizar_categoria("S/C") is None
    assert V.normalizar_categoria("HOSPITAL") is None
    assert V.normalizar_categoria(None) is None


def test_grupo_categoria():
    res = {"II-1", "II-2", "III-1"}
    noc = {"I-1", "I-2", "I-3", "I-4"}
    assert V.grupo_categoria("II-1", res, noc) == "resolutiva"
    assert V.grupo_categoria("I-3", res, noc) == "no_concluyente"
    assert V.grupo_categoria(None, res, noc) == "desconocida"


def test_arreglar_mojibake():
    v, cambio = V.arreglar_mojibake("PUESTO YÿACUCHO")
    assert cambio is True and v == "PUESTO YñACUCHO"
    assert V.arreglar_mojibake("SAN JUAN")[1] is False
    assert V.arreglar_mojibake(None) == (None, False)


# --- validación de coordenadas --------------------------------------
class _QR:
    def __init__(self):
        self.rows = []
    def check(self, *a):
        self.rows.append(a)


def _df(lat, lon):
    return pd.DataFrame({"NORTE": lat, "ESTE": lon, "id": range(len(lat))})


def test_coord_faltantes_y_cero_se_eliminan():
    qr = _QR()
    df = _df([-12.0, None, -8.0, 0.0], [-77.0, -75.0, None, -79.0])
    out = V.validar_coordenadas_xy(df, "NORTE", "ESTE", BBOX, 1e-6, "t", qr)
    assert list(out["id"]) == [0]        # sobra solo el primero
    assert "_lat" in out.columns and "_lon" in out.columns


def test_coord_signo_de_hemisferio_se_corrige():
    qr = _QR()
    df = _df([12.0], [77.0])             # positivas: su negativo cae en Perú
    out = V.validar_coordenadas_xy(df, "NORTE", "ESTE", BBOX, 1e-6, "t", qr)
    assert len(out) == 1
    assert out["_lat"].iloc[0] == pytest.approx(-12.0)
    assert out["_lon"].iloc[0] == pytest.approx(-77.0)


def test_coord_intercambio_lat_lon_se_corrige():
    qr = _QR()
    df = _df([-77.0], [-12.0])           # (lat,lon) fuera; (lon,lat) dentro
    out = V.validar_coordenadas_xy(df, "NORTE", "ESTE", BBOX, 1e-6, "t", qr)
    assert out["_lat"].iloc[0] == pytest.approx(-12.0)
    assert out["_lon"].iloc[0] == pytest.approx(-77.0)


def test_coord_fuera_de_peru_se_elimina():
    qr = _QR()
    df = _df([-12.0, 40.0], [-77.0, -3.0])   # 2do: Europa
    out = V.validar_coordenadas_xy(df, "NORTE", "ESTE", BBOX, 1e-6, "t", qr)
    assert list(out["id"]) == [0]


def test_recuperar_coordenadas_por_ubigeo():
    qr = _QR()
    centroides = {"200101": (-80.6, -5.2), "250301": (-73.77, -10.73)}
    df = pd.DataFrame({
        "NORTE": [None, "-8.4", "0", None],
        "ESTE":  [None, "-74.5", "0", "-74.2"],
        "UBIGEO": ["200101", "200105", "250301", "999999"],   # 1º y 3º recuperables; 4º sin distrito
        "id": [1, 2, 3, 4],
    })
    out = V.recuperar_coordenadas_por_ubigeo(df, "NORTE", "ESTE", "UBIGEO",
                                             centroides, 1e-6, "t", qr)
    assert bool(out.loc[out.id == 1, "coordenada_recuperada"].iloc[0]) is True
    assert float(out.loc[out.id == 1, "ESTE"].iloc[0]) == pytest.approx(-80.6)   # lon
    assert float(out.loc[out.id == 1, "NORTE"].iloc[0]) == pytest.approx(-5.2)   # lat
    assert bool(out.loc[out.id == 3, "coordenada_recuperada"].iloc[0]) is True   # tenía ceros
    assert bool(out.loc[out.id == 2, "coordenada_recuperada"].iloc[0]) is False  # ya tenía coord
    assert bool(out.loc[out.id == 4, "coordenada_recuperada"].iloc[0]) is False  # UBIGEO sin distrito
    # regla registrada con tasa de recuperación (2 de 3 faltantes)
    fila = [x for x in qr.rows if x[1] == "coordenadas_recuperadas_por_ubigeo"][0]
    assert fila[2] == 3 and fila[3] == 2


def test_marcar_duplicados_conserva_primera():
    qr = _QR()
    df = pd.DataFrame({"cod": ["a", "b", "a", "c"], "v": [1, 2, 3, 4]})
    out = V.marcar_duplicados(df, "cod", "t", qr)
    assert list(out["cod"]) == ["a", "b", "c"] and list(out["v"]) == [1, 2, 4]


def test_col_detecta_por_nombre_normalizado():
    assert V._col(["COD_IPRESS", "NOMBRE"], ["codipress"]) == "COD_IPRESS"
    assert V._col(["UBIGEO"], ["cod_ubigeo", "ubigeo"]) == "UBIGEO"
    assert V._col(["X", "Y"], ["ubigeo"]) is None
