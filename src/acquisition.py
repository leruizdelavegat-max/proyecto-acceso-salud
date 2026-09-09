# -*- coding: utf-8 -*-
"""
Fase 1 (a) — Adquisición de datos.

Para cada fuente declarada en `config.md`:
  1. si ya está en `data/raw/`, se usa tal cual (idempotente);
  2. si no, se copia desde `_data/` (caché provista para el curso, declarada
     como `cache_local`) — el caso que pide el enunciado cuando el portal no
     está disponible;
  3. si tampoco hay caché, se intenta la descarga real (RENIPRESS: se busca el
     CSV mensual más reciente; OSM: descarga directa de Geofabrik); si la
     fuente es un portal interactivo sin enlace directo (SIGMED, límites), se
     imprimen instrucciones en vez de fallar en silencio.

Cada intento queda con fecha en `data/raw/download_log.json`.
`data/raw/` nunca se modifica a mano.

    python src/acquisition.py            # resuelve todas las fuentes
    python src/acquisition.py --forzar   # vuelve a copiar/descargar
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]


# ===========================================================================
# Configuración (compartida por acquisition / validation)
# ===========================================================================
def cargar_config(path: Path | str | None = None) -> dict:
    import yaml
    path = Path(path) if path else REPO_ROOT / "config.md"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ruta(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else REPO_ROOT / p


def departamentos_objetivo(cfg: dict) -> dict[str, str]:
    d = cfg["departamentos"]
    return {"costero": d["costero"], "andino": d["andino"], "amazonico": d["amazonico"]}


def codigos_inei(cfg: dict) -> dict[str, str]:
    codigos = cfg["departamentos"]["codigos_inei"]
    return {n: codigos[n] for n in departamentos_objetivo(cfg).values()}


def asegurar_directorios(cfg: dict) -> None:
    for clave in ("raw", "processed", "outputs", "reports"):
        ruta(cfg["rutas"][clave]).mkdir(parents=True, exist_ok=True)


# ===========================================================================
# Lectura controlando el encoding (usada también por validation.py)
# ===========================================================================
def leer_csv_con_encoding(path: Path, sep: str, encoding_esperado: str):
    """Devuelve (df, nota). Intenta el encoding de `config.md`; si falla, cae
    al que detecte `chardet` (típicamente latin-1) y lo documenta."""
    import pandas as pd
    try:
        df = pd.read_csv(path, sep=sep, encoding=encoding_esperado, dtype=str, low_memory=False)
        return df, {"encoding_usado": encoding_esperado,
                    "detalle": "decodifica con el encoding declarado en config.md"}
    except UnicodeDecodeError:
        import chardet
        det = chardet.detect(path.read_bytes()[:200_000])
        enc = det["encoding"] or "latin-1"
        df = pd.read_csv(path, sep=sep, encoding=enc, dtype=str, low_memory=False)
        return df, {"encoding_usado": enc,
                    "detalle": f"{path.name} no decodifica como {encoding_esperado}; "
                               f"chardet detectó {enc} (confianza {det['confidence']:.2f})"}


# ===========================================================================
# Adquisición
# ===========================================================================
def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cargar_log(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _guardar_log(path: Path, log: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")


def _registrar(log, clave, **campos):
    log.setdefault(clave, {}).update({"ultima_verificacion": _ahora(), **campos})


def _copiar(origen: Path, destino: Path) -> int:
    destino.parent.mkdir(parents=True, exist_ok=True)
    if origen.suffix.lower() == ".shp":          # el shapefile arrastra .dbf/.shx/.prj/...
        total = 0
        for lado in origen.parent.glob(origen.stem + ".*"):
            dst = destino.parent / (destino.stem + lado.suffix)
            shutil.copyfile(lado, dst)
            total += dst.stat().st_size
        return total
    shutil.copyfile(origen, destino)
    return destino.stat().st_size


def _descargar(url: str, destino: Path, ua: dict, timeout: int) -> int:
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".part")
    total = 0
    with requests.get(url, headers=ua, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                f.write(chunk)
                total += len(chunk)
    tmp.replace(destino)
    return total


def _reintentar(fn, n: int):
    ultimo = None
    for i in range(1, n + 1):
        try:
            return fn()
        except (requests.RequestException, OSError) as e:
            ultimo = e
            print(f"    intento {i}/{n} falló: {e}")
    raise ultimo


def resolver_fuente(clave: str, cfg_fuente: dict, cfg: dict, log: dict, force: bool) -> None:
    """Resuelve una fuente por las 3 vías (ya existe / caché / descarga)."""
    ua = {"User-Agent": cfg["descargas"]["user_agent"]}
    timeout = cfg["descargas"]["timeout_segundos"]
    reintentos = cfg["descargas"]["reintentos"]
    destino = ruta(cfg_fuente["archivo_local"])

    if destino.exists() and destino.stat().st_size > 0 and not force:
        print(f"[{clave}] ya está en {destino.relative_to(REPO_ROOT)}")
        _registrar(log, clave, estado="ya_existia", archivo_local=str(destino),
                   tamano_bytes=destino.stat().st_size)
        return

    cache = cfg_fuente.get("cache_local")
    if cache and ruta(cache).exists():
        n = _copiar(ruta(cache), destino)
        fecha = datetime.fromtimestamp(ruta(cache).stat().st_mtime,
                                       tz=timezone.utc).isoformat(timespec="seconds")
        print(f"[{clave}] copiado de caché {ruta(cache).name} -> "
              f"{destino.relative_to(REPO_ROOT)} ({n/1e6:.1f} MB)")
        _registrar(log, clave, estado="copiado_desde_cache", archivo_local=str(destino),
                   cache_local=str(ruta(cache)), tamano_bytes=n, fecha_archivo_cache=fecha,
                   fecha_dato=cfg_fuente.get("fecha_cache"))
        return

    tipo = cfg_fuente.get("tipo_descarga")
    try:
        if clave == "renipress":
            print(f"[{clave}] buscando el CSV mensual más reciente en {cfg_fuente['url']} ...")
            html = _reintentar(lambda: requests.get(cfg_fuente["url"], headers=ua, timeout=timeout).text,
                               reintentos)
            cands = sorted(set(re.findall(cfg_fuente["patron_archivo"], html)))
            if not cands:
                raise RuntimeError("ningún enlace calza con patron_archivo")
            fecha_de = lambda s: datetime(*map(int, re.search(r"(\d{2})-(\d{2})-(\d{4})", s).group(3, 2, 1)))
            archivo = max(cands, key=fecha_de)
            url = "https://www.datosabiertos.gob.pe/sites/default/files/" + archivo
            n = _reintentar(lambda: _descargar(url, destino, ua, timeout), reintentos)
            print(f"[{clave}] descargado {archivo} ({n/1e6:.1f} MB)")
            _registrar(log, clave, estado="descargado", archivo_local=str(destino),
                       url=url, tamano_bytes=n)
        elif tipo == "directo":
            n = _reintentar(lambda: _descargar(cfg_fuente["url"], destino, ua, timeout), reintentos)
            print(f"[{clave}] descargado ({n/1e6:.1f} MB)")
            _registrar(log, clave, estado="descargado", archivo_local=str(destino),
                       url=cfg_fuente["url"], tamano_bytes=n)
        else:
            origen = cfg_fuente.get("url") or cfg_fuente.get("fuente")
            print(f"[{clave}] descarga MANUAL: {origen}\n    guardar en: {destino}")
            _registrar(log, clave, estado="manual_requerido", archivo_local=str(destino),
                       url_origen=origen)
    except Exception as e:
        print(f"[{clave}] ERROR: {e}")
        _registrar(log, clave, estado="error", mensaje=str(e))


def run(cfg: dict, force: bool = False) -> dict:
    asegurar_directorios(cfg)
    log_path = ruta(cfg["rutas"]["log_descargas"])
    log = _cargar_log(log_path)
    for clave, cfg_fuente in cfg["fuentes"].items():
        if clave == "limites_administrativos":
            for capa, cfg_capa in cfg_fuente["capas"].items():
                resolver_fuente(f"limites_{capa}",
                                {**cfg_capa, "fuente": cfg_fuente.get("fuente"), "tipo_descarga": "manual"},
                                cfg, log, force)
        else:
            resolver_fuente(clave, cfg_fuente, cfg, log, force)
    _guardar_log(log_path, log)
    print(f"\nRegistro -> {log_path.relative_to(REPO_ROOT)}")
    return log


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fase 1 (a) — adquisición de datos")
    ap.add_argument("--forzar", action="store_true", help="vuelve a copiar/descargar aunque exista")
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    run(cargar_config(a.config), force=a.forzar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
