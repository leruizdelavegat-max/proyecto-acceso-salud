# config.md
# Configuración central del proyecto "La hora dorada"
# Todo el código (notebooks y src/) debe leer de aquí — nada de valores
# hardcodeados en una celda o script.
# EDITA los valores marcados con "# <-- EDITAR" según tu selección final.

departamentos:
  costero: "PIURA" 
  andino: "AYACUCHO"        
  amazonico: "UCAYALI"    
  codigos_inei:
    PIURA: "20"
    AYACUCHO: "05"
    UCAYALI: "25"

rutas:
  raw: "data/raw"
  processed: "data/processed"
  outputs: "data/outputs"
  reports: "logs"
  log_descargas: "data/raw/download_log.json"

descargas:
  # datosabiertos.gob.pe responde HTTP 418 (bloqueo de su WAF) a clientes sin
  # user-agent de navegador. Verificado el 2026-09-08: sin este header la
  # descarga automática falla; con él, funciona. Documentado aquí para que
  # el motivo no quede oculto en el código.
  user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
  timeout_segundos: 60
  reintentos: 3

fuentes:
  # cache_local: copia ya en el repo (carpeta _data/, provista para el curso)
  # que el notebook de Fase 1 usa como caché cuando el portal de origen no
  # está disponible el día que se corre el pipeline -- tal como pide el
  # enunciado. Nunca se modifica: se copia tal cual a archivo_local
  # (data/raw/), que es la ruta que de verdad se valida.
  renipress:
    # Página del dataset (para citar en el informe). El archivo real es un
    # CSV mensual cuyo nombre cambia (RENIPRESS_DD-MM-AAAA.csv); si no hay
    # cache_local, la celda de adquisición busca en esta página el enlace
    # más reciente que calce con patron_archivo y lo descarga.
    url: "https://www.datosabiertos.gob.pe/dataset/registro-nacional-de-entidades-prestadoras-de-servicios-de-salud-renipress"
    patron_archivo: 'RENIPRESS_\d{2}-\d{2}-\d{4}\.csv'
    tipo_descarga: "portal_listado"   # portal_listado | directo | manual
    cache_local: "_data/RENIPRESS_30-04-2026.csv"
    fecha_cache: "2026-04-30"   # fecha del snapshot, no de la copia al repo
    archivo_local: "data/raw/renipress.csv"
    separador: ";"
    encoding: "utf-8-sig"
  sigmed:
    # Portal interactivo (selección manual de área -> genera el shapefile
    # de centros poblados). No expone enlaces de descarga directa ni una
    # API pública conocida.
    url: "https://sigmed.minedu.gob.pe/descargas/"
    tipo_descarga: "manual"
    cache_local: "_data/CP_P.shp"   # shapefile (arrastra .dbf/.shx/.prj/.cpg/.sbn/.sbx)
    archivo_local: "data/raw/centros_poblados.shp"
  osm_peru:
    url: "https://download.geofabrik.de/south-america/peru-latest.osm.pbf"
    tipo_descarga: "directo"
    cache_local: "_data/peru-260907.osm.pbf"
    fecha_cache: "2026-09-07"
    archivo_local: "data/raw/peru-latest.osm.pbf"
  limites_administrativos:
    # Cartografía censal de INEI (V Censo Nacional Económico), en 3 capas.
    fuente: "INEI — Cartografía censal (límites político-administrativos), V Censo Nacional Económico"
    tipo_descarga: "manual"
    capas:
      departamento:
        cache_local: "_data/DEPARTAMENTO.gpkg"
        archivo_local: "data/raw/limites_departamento.gpkg"
      provincia:
        cache_local: "_data/PROVINCIA.gpkg"
        archivo_local: "data/raw/limites_provincia.gpkg"
      distrito:
        cache_local: "_data/DISTRITO.gpkg"
        archivo_local: "data/raw/limites_distrito.gpkg"

categorias:
  resolutivas:
    - "II-1"
    - "II-2"
    - "II-E"
    - "III-1"
    - "III-2"
    - "III-E"
  no_concluyentes:
    - "I-1"
    - "I-2"
    - "I-3"
    - "I-4"

validacion:
  bbox_peru:
    lon_min: -81.4
    lon_max: -68.6
    lat_min: -18.4
    lat_max: -0.04
  # por debajo de este valor absoluto, una coordenada se trata como
  # "cero" (centinela de dato faltante), no como una posición real cercana
  # al (0,0). Ver limpiar_coordenadas() en Fase1_Adquisicion_y_Validacion.ipynb
  tolerancia_coordenada_cero: 0.000001

routing:
  # Fase 2 — motor de ruteo: OSMnx + NetworkX (ver README y src/routing.py).
  # Se descartó OSRM en Docker porque la BIOS de la máquina tiene la
  # virtualización desactivada (WSL2/Docker no arrancan) y `pyrosm` no
  # compila en Python 3.14 sin MSVC Build Tools. Trade-off asumido: la
  # descarga del grafo por Overpass es más lenta y la RAM (8.5 GB) va justa
  # en el grafo a pie de Ayacucho; a cambio, cero dependencias de sistema.
  motor: "networkx"
  grafo:
    cache_dir: "data/interim/graphs"
    # De dónde sale la red vial:
    #   pbf_local      -> se lee data/raw/peru-latest.osm.pbf con pyosmium
    #                     (sin red; robusto; el servidor público de Overpass
    #                     estaba caído/inalcanzable el día de la corrida).
    #   osmnx_overpass -> se descarga por Overpass (fallback).
    fuente: "pbf_local"
    # No se descarga la red de TODO el departamento (Amazonía = casi todo selva
    # sin caminos; Overpass lo parte en decenas de sub-consultas). Se descarga
    # solo la red dentro de este radio (km) de cada punto de demanda muestreado
    # y de cada instalación candidata (unión de buffers, no casco convexo).
    # 20 km alrededor de cada punto: a ~30-40 km/h en vía rural, 60 min de la
    # "hora de oro" ≈ 30-40 km, pero el grafo se arma con el bbox combinado de
    # los 3 deptos (que ya extiende el alcance real muy por encima de 20 km).
    # Limitación a declarar en el informe: una ruta que se desvíe más allá del
    # área queda truncada (el punto sale como no-ruteable, nunca con recta).
    # Subirlo agranda el grafo y la RAM necesaria.
    buffer_km: 20
  velocidades_kmh:
    # el perfil `car` usa maxspeed de OSM y, si falta, la velocidad por tipo
    # de vía (ver _CAR_KMH en src/routing.py). bike y foot son uniformes:
    bike: 15
    foot: 4.5
  snap_umbral_m: 1500            # si el nodo más cercano queda más lejos -> snap_ok=False
  perfiles: ["car", "bike", "foot"]
  perfil_principal: "car"        # define t_min(i) para la Fase 3
  # Solo estos perfiles calculan la matriz COMPLETA origen x instalación
  # (la necesita el simulador de la Fase 4). El resto guarda solo el
  # resolutivo más cercano por origen.
  matriz_completa_perfiles: ["car"]
  # Categorías que son destino en la matriz: resolutivas + candidatas a
  # "upgrade" en el simulador de la Fase 4.
  categorias_matriz: ["II-1", "II-2", "II-E", "III-1", "III-2", "III-E", "I-3", "I-4"]
  umbral_hora_oro_minutos: 60
  bandas_minutos: [30, 60, 120]  # bandas de cobertura para la Fase 3
  muestreo:
    # 4325 + 11144 + 1718 = 17187 centros poblados > 5000 -> hay que muestrear.
    # SIGMED no trae población -> muestreo estratificado por distrito (proporcional).
    tope_puntos_demanda: 5000
    estrategia: "estratificado_por_distrito"
    semilla: 42
  # Fallback para puntos sin ruta: NO se asigna distancia en línea recta;
  # se marcan routable=False. Si se usara la recta, el factor de rodeo debe
  # calibrarse contra las rutas reales (mediana distancia_red / distancia_recta).
  usar_recta_si_no_hay_ruta: false
  factor_rodeo_recta: null
  rutas:
    cache_matriz: "data/outputs/matriz_tiempos.parquet"
    cache_acceso: "data/outputs/acceso_nearest.parquet"
    cache_snap: "data/outputs/routing_snap.parquet"
    reporte_snapping: "logs/snapping_report.csv"
    demanda_muestreada: "data/processed/demanda_muestreada.gpkg"
    log_ejecucion: "logs/routing_run.log"

metrics:
  # Fase 3. Bandas de cobertura (minutos): <30 / 30-60 / 60-120 / >120.
  bandas_minutos: [30, 60, 120]
  n_brechas: 20                    # cuántos distritos lista "brechas críticas"
  # Regla urbano/rural (INEI): urbano si >= 2000 hab. O es capital distrital.
  umbral_urbano_habitantes: 2000
  # Población por centro poblado: SIGMED no la trae. Se estima del raster
  # WorldPop 1 km UN-ajustado (no constrained), asignando cada celda al centro
  # poblado más cercano dentro del polígono de su departamento (el total por
  # departamento coincide con el censo). Fuente a citar:
  #   WorldPop 2020, Peru, UN-adjusted. DOI:10.5258/SOTON/WP00685
  raster_poblacion: "data/raw/peru_worldpop_2020_1km.tif"
  archivo_poblacion: "data/processed/centros_poblados_poblacion.parquet"

estado_operativo_valido:
  - "ACTIVO"
