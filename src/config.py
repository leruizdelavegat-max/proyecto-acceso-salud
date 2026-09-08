"""Carga config.md (un archivo YAML con extensión .md) y expone rutas
absolutas y parámetros del proyecto. Nada en src/ debe tener valores
hardcodeados que pertenezcan a config.md: si un script necesita un
departamento, una ruta, una categoría o un umbral, lo lee de aquí.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.md"


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"{path} no contiene un mapeo YAML válido")
    return cfg


def resolve_path(rel_path: str) -> Path:
    """Convierte una ruta de config.md (relativa al repo) en una Path absoluta."""
    p = Path(rel_path)
    return p if p.is_absolute() else REPO_ROOT / p


CONFIG = load_config()


def departamentos_seleccionados() -> dict[str, str]:
    """{región geográfica: nombre de departamento en mayúsculas} — costero/andino/amazónico."""
    d = CONFIG["departamentos"]
    return {
        "costero": d["costero"],
        "andino": d["andino"],
        "amazonico": d["amazonico"],
    }


def codigos_inei_seleccionados() -> dict[str, str]:
    """{nombre de departamento: código INEI de 2 dígitos} solo para los 3 elegidos."""
    d = CONFIG["departamentos"]
    codigos = d["codigos_inei"]
    nombres = set(departamentos_seleccionados().values())
    faltantes = nombres - set(codigos)
    if faltantes:
        raise ValueError(
            f"config.md: faltan codigos_inei para {faltantes}. "
            "departamentos.costero/andino/amazonico y departamentos.codigos_inei "
            "deben referirse a los mismos 3 departamentos."
        )
    return {n: codigos[n] for n in nombres}


def ensure_dirs() -> None:
    for clave in ("raw", "processed", "outputs", "reports"):
        resolve_path(CONFIG["rutas"][clave]).mkdir(parents=True, exist_ok=True)
