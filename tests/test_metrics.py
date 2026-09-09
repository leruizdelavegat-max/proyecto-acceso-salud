# -*- coding: utf-8 -*-
"""Pruebas de las funciones puras de la Fase 3 (métricas). Sin red ni archivos.

    pytest -q tests/test_metrics.py
"""
import numpy as np
import pandas as pd
import pytest

from src import metrics as M


def _base(n_por_grupo=None):
    """Tabla base mínima: 4 centros poblados con t_min y población."""
    return pd.DataFrame({
        "cod_ccpp": ["a", "b", "c", "d"],
        "t_min": [10.0, 50.0, 90.0, np.nan],          # d = sin ruta
        "poblacion": [1000.0, 100.0, 10.0, 500.0],
        "departamento": ["PIURA", "PIURA", "AYACUCHO", "AYACUCHO"],
        "ubigeo_distrito": ["200101", "200101", "050101", "050102"],
        "nombre_distrito": ["D1", "D1", "D2", "D3"],
        "altitud_m": [50.0, 400.0, 3200.0, 3800.0],
        "CAPITAL": ["1", "0", "0", "0"],
    })


# --- bandas de cobertura -------------------------------------------------
def test_clasificar_bandas():
    s = M.clasificar_bandas(pd.Series([5, 30, 61, 200, np.nan]), [30, 60, 120])
    assert list(s) == ["0-30", "30-60", "60-120", ">120", "sin_ruta"]


def test_bandas_cobertura_share_suma_100():
    cob = M.bandas_cobertura(_base(), [30, 60, 120])
    assert cob["share_pct"].sum() == pytest.approx(100.0, abs=0.05)
    # a (1000 hab, 10 min) domina la banda 0-30
    fila = cob.loc[cob["banda"] == "0-30", "share_pct"].iloc[0]
    assert fila == pytest.approx(100 * 1000 / 1610, abs=0.1)


# --- acceso ponderado --------------------------------------------------
def test_acceso_ponderado_pondera_por_poblacion():
    p = M.acceso_ponderado(_base())
    d1 = p.loc[p["ubigeo_distrito"] == "200101"].iloc[0]
    # (10*1000 + 50*100) / 1100 = 13.64
    assert d1["acceso_min_ponderado"] == pytest.approx(13.64, abs=0.05)
    assert d1["pct_sin_ruta"] == 0.0
    d3 = p.loc[p["ubigeo_distrito"] == "050102"].iloc[0]
    assert np.isnan(d3["acceso_min_ponderado"]) and d3["pct_sin_ruta"] == 100.0


# --- Gini / Lorenz ---------------------------------------------------
def test_gini_igualdad_perfecta_es_cero():
    assert M.gini_ponderado(np.array([20.0, 20.0, 20.0]), np.array([1.0, 5.0, 3.0])) == pytest.approx(0.0, abs=1e-6)


def test_gini_entre_0_y_1_y_crece_con_desigualdad():
    poco = M.gini_ponderado(np.array([10.0, 12.0, 11.0]), np.array([1.0, 1.0, 1.0]))
    mucho = M.gini_ponderado(np.array([1.0, 1.0, 100.0]), np.array([1.0, 1.0, 1.0]))
    assert 0 <= poco < mucho <= 1


def test_lorenz_endpoints():
    lz = M.curva_lorenz(np.array([1.0, 2.0, 3.0, 10.0]), np.array([1.0, 1.0, 1.0, 1.0]))
    assert lz["pob_acumulada"].iloc[0] == 0 and lz["tiempo_acumulado"].iloc[0] == pytest.approx(0)
    assert lz["pob_acumulada"].iloc[-1] == pytest.approx(1) and lz["tiempo_acumulado"].iloc[-1] == pytest.approx(1)


# --- urbano / rural -------------------------------------------------
def test_clasificar_urbano_rural():
    df = pd.DataFrame({"poblacion": [5000, 100, 100], "CAPITAL": ["0", "1", "0"]})
    assert list(M.clasificar_urbano_rural(df, 2000)) == ["urbano", "urbano", "rural"]


# --- cross altitud -------------------------------------------------
def test_cross_altitud_devuelve_franjas_y_correlacion():
    t = M.cross_acceso_altitud(_base())
    assert "franja_altitud" in t.columns
    assert "spearman_t_min_vs_altitud" in t.attrs
    assert t.attrs["interpretacion"] == "correlacional, no causal"


# --- brechas -----------------------------------------------------------
def test_brechas_criticas_ordena_peor_primero_y_sin_dato_arriba():
    pond = M.acceso_ponderado(_base())
    br = M.brechas_criticas(pond, n=3)
    assert br.iloc[0]["rank"] == 1
    # el distrito sin ruta (050102) debe quedar de primero
    assert br.iloc[0]["ubigeo_distrito"] == "050102"
