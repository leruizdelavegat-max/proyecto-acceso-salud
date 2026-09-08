"""Fase 1 — Adquisición de datos.

Descarga (o verifica en caché) las 4 fuentes declaradas en config.md:
RENIPRESS, SIGMED (centros poblados), red vial OSM y límites
administrativos (departamento/provincia/distrito).

Reglas de comportamiento (exigidas por el enunciado):
- Idempotente: si el archivo ya existe en data/raw/, no se vuelve a tocar
  (salvo --force).
- Nunca escribe en data/raw/ un archivo a medias: se copia/descarga a un
  .part y se renombra al terminar.
- Si config.md declara un `cache_local` (una copia ya provista en el
  repo, en _data/, para el día en que el portal de origen no esté
  disponible), se usa esa copia en vez de ir a internet. El archivo de
  _data/ nunca se modifica: solo se copia a data/raw/.
- Si no hay cache_local ni el archivo ya existe, se intenta la descarga
  automática (RENIPRESS, OSM) o se informan instrucciones claras de
  descarga manual (SIGMED, límites administrativos).
- Cada intento (exitoso o no) queda registrado en
  rutas.log_descargas (data/raw/download_log.json) con fecha, estado y
  origen, para poder documentar en el informe cuándo y de dónde se
  obtuvo cada dato.

Uso:
    python src/acquisition.py                # todas las fuentes
    python src/acquisition.py --fuente osm_peru
    python src/acquisition.py --force         # vuelve a obtener todo
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from config import CONFIG, ensure_dirs, resolve_path

FUENTES_VALIDAS = ("renipress", "sigmed", "osm_peru", "limites_administrativos")


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cargar_log(ruta_log: Path) -> dict[str, Any]:
    if ruta_log.exists():
        try:
            return json.loads(ruta_log.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _guardar_log(ruta_log: Path, log: dict[str, Any]) -> None:
    ruta_log.parent.mkdir(parents=True, exist_ok=True)
    ruta_log.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")


def _registrar(log: dict[str, Any], clave: str, **campos: Any) -> None:
    entrada = log.setdefault(clave, {})
    entrada["ultima_verificacion"] = _ahora_iso()
    entrada.update(campos)


def _sesion_http() -> requests.Session:
    s = requests.Session()
    # datosabiertos.gob.pe (RENIPRESS) devuelve HTTP 418 a clientes sin
    # user-agent de navegador (bloqueo del WAF) — ver config.md:descargas.
    s.headers.update({"User-Agent": CONFIG["descargas"]["user_agent"]})
    return s


def _copiar_archivo(origen: Path, destino: Path) -> int:
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".part")
    shutil.copyfile(origen, tmp)
    tmp.replace(destino)
    return destino.stat().st_size


def _copiar_shapefile(origen_shp: Path, destino_shp: Path) -> int:
    """Un .shp viene siempre acompañado de sidecars (.dbf/.shx/.prj/...).
    Se copian todos los archivos que comparten el mismo nombre base."""
    destino_shp.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    for sidecar in origen_shp.parent.glob(origen_shp.stem + ".*"):
        total += _copiar_archivo(sidecar, destino_shp.parent / (destino_shp.stem + sidecar.suffix))
    return total


def _descargar_a_archivo(sesion: requests.Session, url: str, destino: Path) -> int:
    """Descarga en streaming a un .part y renombra. Devuelve bytes escritos."""
    timeout = CONFIG["descargas"]["timeout_segundos"]
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".part")
    total = 0
    with sesion.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
                    total += len(chunk)
    tmp.replace(destino)
    return total


def _con_reintentos(fn, reintentos: int):
    ultimo_error = None
    for intento in range(1, reintentos + 1):
        try:
            return fn()
        except (requests.RequestException, OSError) as e:
            ultimo_error = e
            print(f"    intento {intento}/{reintentos} falló: {e}")
    raise ultimo_error


def _usar_cache_local(clave: str, cfg: dict[str, Any], destino: Path, log: dict[str, Any]) -> bool:
    """Si config.md declara cache_local y existe, copia esa copia (provista
    en _data/ para cuando el portal de origen no esté disponible) a
    data/raw/. Devuelve True si pudo resolverse así."""
    cache_local = cfg.get("cache_local")
    if not cache_local:
        return False
    ruta_cache = resolve_path(cache_local)
    if not ruta_cache.exists():
        return False

    es_shapefile = ruta_cache.suffix.lower() == ".shp"
    n_bytes = _copiar_shapefile(ruta_cache, destino) if es_shapefile else _copiar_archivo(ruta_cache, destino)
    fecha_archivo = datetime.fromtimestamp(ruta_cache.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
    print(f"[{clave}] copiado desde caché local {ruta_cache} -> {destino} ({n_bytes / 1e6:.1f} MB)")
    _registrar(
        log, clave, estado="copiado_desde_cache_local", archivo_local=str(destino),
        cache_local=str(ruta_cache), tamano_bytes=n_bytes,
        fecha_archivo_cache=fecha_archivo, fecha_dato=cfg.get("fecha_cache"),
        fecha_incorporacion_al_repo=_ahora_iso(),
    )
    return True


def descargar_renipress(sesion: requests.Session, log: dict[str, Any], force: bool) -> None:
    clave = "renipress"
    cfg = CONFIG["fuentes"][clave]
    destino = resolve_path(cfg["archivo_local"])
    reintentos = CONFIG["descargas"]["reintentos"]

    if destino.exists() and destino.stat().st_size > 0 and not force:
        print(f"[{clave}] ya existe en {destino}, no se vuelve a obtener (usa --force para forzar).")
        _registrar(log, clave, estado="ya_existia", archivo_local=str(destino), tamano_bytes=destino.stat().st_size)
        return

    if _usar_cache_local(clave, cfg, destino, log):
        return

    print(f"[{clave}] sin caché local; buscando el CSV mensual más reciente en {cfg['url']} ...")
    try:
        def listar():
            r = sesion.get(cfg["url"], timeout=CONFIG["descargas"]["timeout_segundos"])
            r.raise_for_status()
            return r.text

        html = _con_reintentos(listar, reintentos)
        patron = cfg["patron_archivo"]
        candidatos = sorted(set(re.findall(patron, html)))
        if not candidatos:
            raise RuntimeError("no se encontró ningún enlace que calce con patron_archivo en la página del dataset")

        def fecha_de(nombre: str) -> datetime:
            dd, mm, aaaa = re.search(r"(\d{2})-(\d{2})-(\d{4})", nombre).groups()
            return datetime(int(aaaa), int(mm), int(dd))

        mas_reciente = max(candidatos, key=fecha_de)
        url_archivo = "https://www.datosabiertos.gob.pe/sites/default/files/" + mas_reciente

        print(f"[{clave}] descargando {mas_reciente} ...")
        n_bytes = _con_reintentos(lambda: _descargar_a_archivo(sesion, url_archivo, destino), reintentos)
        print(f"[{clave}] descargado ({n_bytes / 1e6:.1f} MB) -> {destino}")
        _registrar(
            log, clave, estado="descargado", archivo_local=str(destino),
            archivo_origen=mas_reciente, url=url_archivo, tamano_bytes=n_bytes, fecha_descarga=_ahora_iso(),
        )
    except Exception as e:
        print(f"[{clave}] ERROR: {e}")
        _registrar(log, clave, estado="error", mensaje=str(e), url=cfg["url"])
        if not (destino.exists() and destino.stat().st_size > 0):
            print(
                f"[{clave}] no hay copia disponible. Descarga manual: {cfg['url']} "
                f"y guarda el CSV en {destino} (separador '{cfg['separador']}')."
            )


def descargar_directo(nombre: str, sesion: requests.Session, log: dict[str, Any], force: bool) -> None:
    cfg = CONFIG["fuentes"][nombre]
    destino = resolve_path(cfg["archivo_local"])
    reintentos = CONFIG["descargas"]["reintentos"]

    if destino.exists() and destino.stat().st_size > 0 and not force:
        print(f"[{nombre}] ya existe en {destino}, no se vuelve a obtener (usa --force para forzar).")
        _registrar(log, nombre, estado="ya_existia", archivo_local=str(destino), tamano_bytes=destino.stat().st_size)
        return

    if _usar_cache_local(nombre, cfg, destino, log):
        return

    print(f"[{nombre}] sin caché local; descargando {cfg['url']} ...")
    try:
        n_bytes = _con_reintentos(lambda: _descargar_a_archivo(sesion, cfg["url"], destino), reintentos)
        print(f"[{nombre}] descargado ({n_bytes / 1e6:.1f} MB) -> {destino}")
        _registrar(log, nombre, estado="descargado", archivo_local=str(destino),
                   url=cfg["url"], tamano_bytes=n_bytes, fecha_descarga=_ahora_iso())
    except Exception as e:
        print(f"[{nombre}] ERROR: {e}")
        _registrar(log, nombre, estado="error", mensaje=str(e), url=cfg["url"])
        if not (destino.exists() and destino.stat().st_size > 0):
            print(f"[{nombre}] no hay copia disponible. Descarga manual: {cfg['url']} -> guardar en {destino}.")


def verificar_manual(nombre: str, cfg: dict[str, Any], log: dict[str, Any], force: bool = False) -> None:
    """Fuentes que son portales interactivos sin enlace de descarga directa
    (SIGMED, límites administrativos). Se resuelven con cache_local si
    existe; si no, se documentan instrucciones de descarga manual en vez
    de fallar en silencio."""
    destino = resolve_path(cfg["archivo_local"])
    if destino.exists() and destino.stat().st_size > 0 and not force:
        print(f"[{nombre}] ya existe en {destino}, no se vuelve a obtener (usa --force para forzar).")
        _registrar(log, nombre, estado="ya_existia", archivo_local=str(destino), tamano_bytes=destino.stat().st_size)
        return

    if _usar_cache_local(nombre, cfg, destino, log):
        return

    origen = cfg.get("url") or cfg.get("fuente")
    print(
        f"[{nombre}] requiere descarga manual: {origen}\n"
        f"    Guarda el archivo en: {destino}\n"
        f"    (config.md -> fuentes.{nombre})"
    )
    _registrar(log, nombre, estado="manual_requerido", archivo_local=str(destino), url_origen=origen)


def procesar_fuente(nombre: str, sesion: requests.Session, log: dict[str, Any], force: bool) -> None:
    cfg = CONFIG["fuentes"][nombre]

    if nombre == "limites_administrativos":
        for capa, cfg_capa in cfg["capas"].items():
            clave = f"limites_administrativos.{capa}"
            cfg_capa = {**cfg_capa, "url": cfg.get("fuente"), "fuente": cfg.get("fuente")}
            verificar_manual(clave, cfg_capa, log, force)
        return

    tipo = cfg["tipo_descarga"]
    if nombre == "renipress":
        descargar_renipress(sesion, log, force)
    elif tipo == "directo":
        descargar_directo(nombre, sesion, log, force)
    elif tipo == "manual":
        verificar_manual(nombre, cfg, log, force)
    else:
        raise ValueError(f"tipo_descarga desconocido para {nombre}: {tipo}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fuente", choices=FUENTES_VALIDAS, default=None,
                         help="obtener solo esta fuente (por defecto: todas)")
    parser.add_argument("--force", action="store_true", help="volver a obtener aunque ya exista")
    args = parser.parse_args(argv)

    ensure_dirs()
    ruta_log = resolve_path(CONFIG["rutas"]["log_descargas"])
    log = _cargar_log(ruta_log)
    sesion = _sesion_http()

    fuentes = [args.fuente] if args.fuente else list(FUENTES_VALIDAS)
    for nombre in fuentes:
        procesar_fuente(nombre, sesion, log, args.force)
        _guardar_log(ruta_log, log)

    print(f"\nRegistro de descargas actualizado en {ruta_log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
