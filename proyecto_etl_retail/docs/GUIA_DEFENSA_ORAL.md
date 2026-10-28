# Guía para la defensa oral (5 a 7 minutos por equipo + preguntas individuales)

La rúbrica evalúa el **dominio técnico individual de los 3 integrantes** (15 pts junto con la resiliencia). Cada quien debe poder explicar **su parte y cómo encaja con las demás**, sin leer el código. Abajo: guion sugerido y preguntas probables con respuesta corta. **Ajusta las cifras con las de tu reporte final.**

## Guion de 6 minutos

| Min | Quién | Qué mostrar |
|---|---|---|
| 0:00–0:45 | Todos / Ing. 1 | Problema y diagrama de flujo (`docs/diagrama_flujo.png`): 3 fuentes sucias → 7 fases → 2 tablas. |
| 0:45–2:15 | Ing. 1 | Perfilado, MCAR/MAR, mediana vs. media (mostrar media/mediana y asimetría del reporte), capping IQR y antes/después. |
| 2:15–3:30 | Ing. 2 | Fechas mixtas → ISO, USD → MXN @ 19.50, cruce con catálogo y huérfanos, Min-Max y terciles. |
| 3:30–5:15 | Ing. 3 | Esquema Pydantic, un registro rechazado con su `motivo_rechazo_cuarentena`, transacciones y **demo en vivo** de `test_estres.py`. |
| 5:15–6:00 | Todos | Reconciliación de filas, límites conocidos, conclusión. |

Demo en vivo recomendada: `python3 pipeline.py` y después abrir la BD (`sqlite3 salida/ventas_retail.db "SELECT motivo_rechazo_cuarentena, COUNT(*) FROM cuarentena_errores GROUP BY 1;"`).

## Ingeniero/a 1 — Calidad, nulos y outliers

1. **¿Por qué mediana y no media?** Las variables son asimétricas a la derecha (bots y compras gigantes); la media se infla y la mediana es robusta. Lo muestro con media/mediana y asimetría del reporte.
2. **¿Cómo distingues MCAR de MAR?** Creo un indicador de "falta" por columna y pruebo si se asocia con otras variables (Mann-Whitney para numéricas, χ² para categóricas) con Bonferroni. Si hay asociación es MAR (ej. el correo falta más cuando el pago es en efectivo); si no, MCAR. MNAR no se puede probar con los datos observados.
3. **¿Por qué recortar y no eliminar outliers?** Eliminar pierde la venta (un bot sigue siendo una transacción); el capping la conserva en el límite del IQR y la bandera `cantidad:capeada` deja evidencia.
4. **¿Por qué los límites son por moneda?** Un precio de 15 USD y uno de 300 MXN no son comparables; un solo IQR confundiría los dos. Se convierte después.
5. **¿Qué es el IQR y cómo se calculan los límites?** `IQR = Q3 − Q1`; límites `Q1 − 1.5·IQR` y `Q3 + 1.5·IQR`.
6. **Si `monto_total` es nulo, ¿qué haces?** Primero recalculo `cantidad × precio_unitario`; solo si falta algo más uso la mediana.
7. **¿Qué eliminas y por qué?** Solo duplicados exactos y `cantidad ≤ 0` (valor imposible según la guía); todo queda en `descartados_ing1.csv` con motivo. En los datos oficiales fueron 20 duplicados del POS y 11 cantidades ≤ 0 (−1 y 0) de la tienda física.
8. **En el JSON hay totales ×80 con unidades normales, ¿qué hiciste?** Detecté que `total_amount ≠ units × unit_price` en 12 pedidos (todos exactamente ×80, firma de bot). Como los otros 588 cumplen la identidad, recalculé el monto con `unidades × precio` y lo dejé marcado (`monto_total:recalculado_por_incoherencia`). Después apliqué el IQR al resto.
9. **¿Y los bots de la tienda física?** Compran 99 o 500 piezas con el total coherente. Ahí el outlier está en `cantidad`: se recorta con IQR (entero, techo de 8) y el monto se recalcula.

## Ingeniero/a 2 — Transformación e integración

1. **Fechas `03/04/2026`: ¿3 de abril o 4 de marzo?** Convención día/mes/año (formato mexicano). En los datos oficiales hay 188 fechas con día > 12 y ninguna con mes > 12, así que la convención se sostiene con evidencia. El reporte cuantifica cuántas son ambiguas; las imposibles quedan nulas y se rechazan.
1b. **¿Qué es ese `1789105020` en `order_timestamp`?** Un timestamp epoch (segundos desde 1970). 208 de los 600 pedidos online lo traen así; se convierte a fecha local de Colima (UTC-6) y se marca `fecha:convertida_a_ISO`. La zona es un supuesto: con UTC una fila quedaría fuera del rango de fechas del archivo y 49 de 208 cambiarían de día (`config.HORAS_UTC_EPOCH`).
2. **¿Cómo conviertes la moneda?** `precio_unitario` y `monto_total` × 19.50 solo si `moneda = USD`; guardo `moneda_original` para auditar.
3. **¿Qué es un registro huérfano?** Una transacción cuyo `id_producto` no existe en el catálogo maestro (o es nulo). Se marca con `es_huerfano` y la validación lo manda a cuarentena.
4. **¿Qué hace Min-Max?** `x' = (x − min)/(max − min)`, deja el importe en [0, 1] para compararlo en la misma escala; si max = min todo queda en 0 (evita dividir entre cero).
5. **¿Por qué terciles para Bajo/Medio/Alto?** Dan tres segmentos equilibrados, robustos a la asimetría, sin umbrales arbitrarios; hay un modo de reglas de negocio con `cortes=(a, b)`.
6. **¿Qué pasa si el catálogo trae llaves duplicadas?** Se limpia (espacios/mayúsculas), se descartan nulas y se conserva la primera de las duplicadas para que el cruce sea uno-a-uno.

## Ingeniero/a 3 — Validación, resiliencia y BD

1. **¿Cómo funciona la cuarentena?** Cada registro se valida solo con el modelo Pydantic; si lanza `ValidationError` lo capturo con `try/except` y lo agrego a cuarentena con `motivo_rechazo_cuarentena` (campo + causa + valor). El lote sigue.
2. **¿Qué validan `@field_validator` y `@model_validator`?** El primero revisa un campo (formato de correo, fecha ISO, huérfano); el segundo una regla entre campos (`monto ≈ cantidad × precio`, 1 % de tolerancia).
3. **¿Qué es una transacción y cómo la usas?** `BEGIN` → inserciones → `COMMIT`; si algo catastrófico falla, `ROLLBACK` y la BD queda como estaba. Cada INSERT va en un `SAVEPOINT`: si la BD rechaza uno, solo ese revierte y pasa a cuarentena (`ERROR_BD`).
4. **¿Qué pasa si corro el pipeline dos veces?** No duplica: la carga reemplaza el contenido dentro de la misma transacción.
5. **¿Cómo pruebas que no hay "crash"?** `test_estres.py`: 23 escenarios (35 % y 85 % de celdas corruptas, archivos vacíos/truncados, SQL injection, fechas imposibles, valores 1e308, BD inaccesible…). Verifica que no haya excepciones, que las filas cuadren y que `integrity_check` sea ok.
6. **¿Cómo te proteges de SQL injection?** Todas las sentencias van parametrizadas; el texto malicioso se guarda como dato (hay una prueba que lo demuestra).
7. **¿Cómo sabes que no se perdió ningún registro?** Reconciliación: `entrada = descartadas + limpias + cuarentena`, verificada en cada corrida contra los conteos reales de la BD.

## Preguntas para todos

- ¿Qué cambiarías si el volumen fuera de 100 millones de filas? (procesamiento por lotes/chunks, `executemany`, PostgreSQL, validación paralela).
- ¿Qué limitaciones tiene su pipeline? (ver `REGLAS_DE_NEGOCIO.md §5`: IQR con cantidades pequeñas, moneda nula asumida MXN, fechas D/M/A, MNAR no verificable).
- ¿Qué datos del catálogo oficial los sorprendieron?
