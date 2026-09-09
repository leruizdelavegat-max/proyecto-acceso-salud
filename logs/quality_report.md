# Informe de Calidad de Datos — Fase 1
Generado: 2026-09-09T13:12:51+00:00

| Dataset | Regla | Evaluados | Marcados | % | Acción | Motivo |
|---|---|---:|---:|---:|---|---|
| renipress | encoding_archivo | 1 | 0 | 0.0% | leído como utf-8-sig | decodifica con el encoding declarado en config.md |
| renipress | encoding_texto_utf8_vs_latin1 | 35471 | 4 | 0.01% | corregido | mojibake conocido (ÿ→ñ) y doble codificación utf-8/latin-1 |
| renipress | coordenadas_recuperadas_por_ubigeo | 13154 | 13154 | 100.0% | corregido (centroide del distrito declarado) — tasa de recuperación 100.0% | coordenada faltante/cero pero con UBIGEO que calza con un distrito de INEI; se ubica en el centroide de ese distrito (ubicación aproximada, marcada) |
| renipress | coordenadas_vacias_o_no_numericas | 35471 | 0 | 0.0% | eliminado | sin coordenada numérica no se puede rutear en la Fase 2 |
| renipress | coordenadas_en_cero | 35471 | 0 | 0.0% | eliminado | /valor/ < 1e-06 se trata como nulo, no como posición cerca de (0,0) |
| renipress | signo_de_hemisferio_invertido | 35471 | 0 | 0.0% | corregido | coordenada positiva cuyo valor negativo sí cae dentro de Perú |
| renipress | coordenadas_lat_lon_intercambiadas | 35471 | 0 | 0.0% | corregido | (lat,lon) cae fuera de Perú pero (lon,lat) cae dentro: se intercambian |
| renipress | coordenadas_fuera_de_peru | 35471 | 0 | 0.0% | eliminado | fuera de lon[-81.4,-68.6] x lat[-18.4,-0.04] |
| renipress | codigos_duplicados | 35471 | 0 | 0.0% | eliminado (se conserva la 1ª aparición) | 'COD_IPRESS' debe identificar un único registro |
| renipress | categoria_no_reconocida | 35471 | 8504 | 23.97% | conservado con advertencia | CATEGORIA vacía, '0' o que no calza con el patrón romano+sufijo |
| renipress | punto_fuera_de_su_poligono_distrital | 35471 | 1683 | 4.74% | conservado con advertencia | el punto no cae en el polígono del distrito que declara su UBIGEO |
| centros_poblados | geometria_vacia | 153400 | 0 | 0.0% | eliminado | sin geometría no se puede rutear |
| centros_poblados | coordenadas_fuera_de_peru | 153400 | 0 | 0.0% | eliminado | el punto representativo cae fuera del bbox de Perú |
| centros_poblados | encoding_texto_utf8_vs_latin1 | 153400 | 0 | 0.0% | sin problemas | mojibake conocido (ÿ→ñ) y doble codificación utf-8/latin-1 |
| centros_poblados | codigos_duplicados | 153400 | 0 | 0.0% | eliminado (se conserva la 1ª aparición) | 'CODCP' debe identificar un único registro |
| centros_poblados | punto_fuera_de_su_poligono_distrital | 153400 | 811 | 0.53% | conservado con advertencia | el punto no cae en el polígono del distrito que declara su UBIGEO |
