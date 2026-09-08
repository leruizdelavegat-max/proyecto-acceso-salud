# config.md
# Configuración central del proyecto "La hora dorada"
# Todo el código debe leer de aquí — nada de valores hardcodeados en src/
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
  # que acquisition.py usa como caché cuando el portal de origen no está
  # disponible el día que se corre el pipeline -- tal como pide el
  # enunciado. Nunca se modifica: se copia tal cual a archivo_local
  # (data/raw/), que es la ruta que lee validation.py.
  renipress:
    # Página del dataset (para citar en el informe). El archivo real es un
    # CSV mensual cuyo nombre cambia (RENIPRESS_DD-MM-AAAA.csv); si no hay
    # cache_local, el script de adquisición busca en esta página el enlace
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
  # al (0,0). Ver src/validation.py::limpiar_coordenadas
  tolerancia_coordenada_cero: 0.000001

routing:
  motor: "networkx"   # <-- EDITAR cuando decidamos Fase 2 (opciones: networkx, osrm, valhalla, graphhopper)
  umbral_hora_oro_minutos: 60
  cache_matriz: "data/outputs/matriz_tiempos.parquet"

estado_operativo_valido:
  - "ACTIVO"
