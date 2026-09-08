# Informe de calidad de datos — renipress_y_centros_poblados
Generado: 2026-09-08T15:51:23+00:00

| Regla | Registros marcados | % | Acción | Motivo |
|---|---|---|---|---|
| codificacion_archivo | 0 | — | leído como utf-8-sig | el archivo decodifica correctamente con el encoding declarado en config.md |
| codificacion_texto_utf8_vs_latin1 | 4 | — | corregido | reemplazo de mojibake conocido (ÿ->ñ, Ÿ->Ñ) y reversión de doble codificación UTF-8/Latin-1 |
| coordenadas_faltantes_o_nulas | 13147 | 37.06% | eliminado | sin coordenada numérica no se puede calcular la distancia por carretera (Fase 2) |
| coordenadas_cero | 7 | 0.02% | eliminado | /valor/ < 1e-06 se trata como centinela de nulo, no coordenada real |
| coordenadas_lat_lon_intercambiadas | 0 | 0.0% | corregido | el par (lat,lon) cae fuera de Perú pero (lon,lat) cae dentro: se intercambian los valores |
| coordenadas_fuera_de_peru | 0 | 0.0% | eliminado | fuera de lon[-81.4,-68.6] x lat[-18.4,-0.04] y no se explica por un intercambio lat/lon |
| codigos_duplicados | 0 | 0.0% | eliminado (se conserva la primera aparición) | 'COD_IPRESS' debe identificar un único establecimiento/centro poblado |
| categoria_no_reconocida | 2988 | 13.39% | conservado con advertencia (excluido del cálculo de más cercano) | CATEGORIA vacía, '0' o que no calza con el patrón romano-sufijo |
| resumen_renipress | 13154 | 37.08% | eliminado (acumulado de las reglas anteriores) | 22317 de 35471 registros conservados (62.9%) |
| puntos_fuera_de_su_poligono_distrital | 1683 | 7.54% | conservado con advertencia | el punto no cae dentro del polígono del distrito que declara su propio UBIGEO |
| geometria_faltante_o_vacia | 0 | 0.0% | eliminado | sin geometría no se puede calcular distancia por carretera |
| codificacion_texto_utf8_vs_latin1 | 0 | — | sin problemas detectados | reemplazo de mojibake conocido (ÿ->ñ, Ÿ->Ñ) y reversión de doble codificación UTF-8/Latin-1 |
| coordenadas_fuera_de_peru | 0 | 0.0% | eliminado | el punto representativo del centro poblado cae fuera del bbox de Perú |
| codigos_duplicados | 0 | 0.0% | eliminado (se conserva la primera aparición) | 'CODCP' debe identificar un único establecimiento/centro poblado |
| puntos_fuera_de_su_poligono_distrital | 811 | 0.53% | conservado con advertencia | el punto no cae dentro del polígono del distrito que declara su propio UBIGEO |
