# config.md
# Configuración central del proyecto "La hora dorada"
# Todo el código debe leer de aquí — nada de valores hardcodeados en src/
# EDITA los valores marcados con "# <-- EDITAR" según tu selección final.

departamentos:
  costero: "PIURA"        # <-- EDITAR si eliges otro
  andino: "CUSCO"         # <-- EDITAR si eliges otro
  amazonico: "LORETO"     # <-- EDITAR si eliges otro
  codigos_inei:
    PIURA: "20"
    CUSCO: "08"
    LORETO: "16"

rutas:
  raw: "data/raw"
  processed: "data/processed"
  outputs: "data/outputs"
  reports: "logs"
  log_descargas: "data/raw/download_log.json"

fuentes:
  renipress:
    url: "https://www.datosabiertos.gob.pe/dataset/registro-nacional-de-entidades-prestadoras-de-servicios-de-salud-renipress"
    archivo_local: "data/raw/renipress.csv"
  sigmed:
    url: "https://sigmed.minedu.gob.pe/descargas/"
    archivo_local: "data/raw/centros_poblados.geojson"
  osm_peru:
    url: "https://download.geofabrik.de/south-america/peru-latest.osm.pbf"
    archivo_local: "data/raw/peru-latest.osm.pbf"
  limites_administrativos:
    fuente: "DECLARAR AQUÍ LA FUENTE OFICIAL USADA (ej. IGN, INEI GEOGPS)"  # <-- EDITAR
    archivo_local: "data/raw/limites_admin.gpkg"

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

routing:
  motor: "networkx"   # <-- EDITAR cuando decidamos Fase 2 (opciones: networkx, osrm, valhalla, graphhopper)
  umbral_hora_oro_minutos: 60
  cache_matriz: "data/outputs/matriz_tiempos.parquet"

estado_operativo_valido:
  - "ACTIVO"
