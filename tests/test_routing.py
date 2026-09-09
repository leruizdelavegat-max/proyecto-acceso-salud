# -*- coding: utf-8 -*-
"""Pruebas del módulo de ruteo que NO necesitan OSRM ni red.

    pytest -q tests/test_routing.py

Cubren las funciones puras que el enunciado pide "importables y testeables de
forma independiente del pipeline": muestreo, selección del más cercano,
conversión de respuestas OSRM y round-trip de la caché Parquet.
"""
import numpy as np
import pandas as pd
import pytest

from src import routing as R


# --------------------------------------------------------------------------
def test_haversine_simetrica_y_escala():
    d = R.haversine_m(-77.0, -12.0, -77.0, -13.0)   # 1° de latitud
    assert 110_000 < d < 112_000
    assert R.haversine_m(0, 0, 1, 1) == pytest.approx(R.haversine_m(1, 1, 0, 0))


def test_a_float2d_maneja_null():
    out = R._a_float2d([[1.0, None], [None, 4.0]], (2, 2))
    assert out.shape == (2, 2)
    assert out[0, 0] == 1.0 and np.isnan(out[0, 1]) and np.isnan(out[1, 0])
    assert np.isnan(R._a_float2d(None, (1, 3))).all()


# --------------------------------------------------------------------------
def _demanda_falsa(n_por_distrito):
    filas = []
    for dist, k in n_por_distrito.items():
        for j in range(k):
            filas.append({"CODCP": f"{dist}-{j}", "UBIGEO": dist})
    return pd.DataFrame(filas)


def test_muestreo_respeta_tope_y_es_estratificado():
    df = _demanda_falsa({"200101": 5000, "200102": 8000, "050301": 4000})  # 17000
    m1, rep = R.muestrear_demanda(df, tope=5000, semilla=42, col_distrito="UBIGEO")
    assert abs(len(m1) - 5000) <= 3            # reparto de restos: exacto salvo redondeo
    assert rep["estrategia"].startswith("estratificado")
    assert set(m1["UBIGEO"]) == {"200101", "200102", "050301"}   # ningún distrito perdido
    # proporcionalidad aproximada
    frac = len(m1) / len(df)
    por_d = m1["UBIGEO"].value_counts()
    assert por_d["200102"] > por_d["200101"] > por_d["050301"]
    assert por_d["200102"] == pytest.approx(8000 * frac, rel=0.05)


def test_muestreo_determinista():
    df = _demanda_falsa({"A": 3000, "B": 3000})
    a, _ = R.muestrear_demanda(df, 2000, semilla=7, col_distrito="UBIGEO")
    b, _ = R.muestrear_demanda(df, 2000, semilla=7, col_distrito="UBIGEO")
    assert list(a["CODCP"]) == list(b["CODCP"])


def test_muestreo_sin_muestreo_si_cabe():
    df = _demanda_falsa({"A": 100})
    m, rep = R.muestrear_demanda(df, 5000, col_distrito="UBIGEO")
    assert len(m) == 100 and rep["fraccion"] == 1.0 and m["en_muestra"].all()


# --------------------------------------------------------------------------
def test_acceso_mas_cercano_elige_min_y_marca_no_ruteables():
    long = pd.DataFrame([
        # origen O1: 2 destinos ruteables -> gana F2 (10 min)
        ("O1", "F1", "car", 1200.0, 20000.0),
        ("O1", "F2", "car",  600.0,  9000.0),
        # origen O2: sin ninguna ruta -> routable=False
        ("O2", "F1", "car", np.nan, np.nan),
        ("O2", "F2", "car", np.nan, np.nan),
        # mismo O1 en perfil foot
        ("O1", "F1", "foot", 9000.0, 8000.0),
    ], columns=["cod_ccpp", "cod_ipress", "profile", "duration_s", "distance_m"])

    acc = R.acceso_mas_cercano(long)
    car_o1 = acc[(acc.cod_ccpp == "O1") & (acc.profile == "car")].iloc[0]
    assert car_o1["cod_destino_cercano"] == "F2"
    assert car_o1["duration_min"] == pytest.approx(10.0)
    assert bool(car_o1["routable"]) is True

    o2 = acc[(acc.cod_ccpp == "O2") & (acc.profile == "car")].iloc[0]
    assert bool(o2["routable"]) is False
    assert pd.isna(o2["duration_min"])

    assert {"car", "foot"} <= set(acc["profile"])


# --------------------------------------------------------------------------
def test_cache_parquet_round_trip_y_dedupe(tmp_path):
    pytest.importorskip("pyarrow")
    p = tmp_path / "matriz.parquet"
    d1 = pd.DataFrame([("O1", "F1", "car", 100.0, 1000.0)],
                      columns=R._CLAVES_MATRIZ + ["duration_s", "distance_m"])
    R._guardar_cache(d1, p)
    # reescribe el mismo par con otro valor -> debe quedarse el último
    d2 = pd.DataFrame([("O1", "F1", "car", 222.0, 2222.0),
                       ("O2", "F1", "car", 300.0, 3000.0)],
                      columns=R._CLAVES_MATRIZ + ["duration_s", "distance_m"])
    comb = R._guardar_cache(d2, p)
    leido = R._cargar_cache(p)
    assert len(leido) == 2
    fila = leido[(leido.cod_ccpp == "O1")].iloc[0]
    assert fila["duration_s"] == 222.0


# --------------------------------------------------------------------------
def test_travel_time_s():
    # 1000 m a 36 km/h = 10 m/s -> 100 s
    assert R.travel_time_s(1000, 36) == pytest.approx(100.0)
    assert R.travel_time_s(500, 0) == float("inf")       # velocidad inválida


def test_parse_maxspeed():
    assert R._parse_maxspeed("50") == pytest.approx(50)
    assert R._parse_maxspeed("30 km/h") == pytest.approx(30)
    assert R._parse_maxspeed("25 mph") == pytest.approx(25 * 1.60934)
    assert R._parse_maxspeed(["80", "60"]) == pytest.approx(60)   # el menor
    assert R._parse_maxspeed("none") is None
    assert R._parse_maxspeed(None) is None


def test_velocidad_car_kmh_usa_maxspeed_luego_tipo_de_via():
    assert R.velocidad_car_kmh("residential", "40") == pytest.approx(40)      # maxspeed gana
    assert R.velocidad_car_kmh("residential", None) == pytest.approx(30)      # cae al tipo
    assert R.velocidad_car_kmh(["primary", "secondary"], None) == pytest.approx(60)
    assert R.velocidad_car_kmh("no_existe", None) == pytest.approx(30)        # default
