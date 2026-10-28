# Pipeline ETL robusto con zona de cuarentena — Retail omnicanal

Pipeline end-to-end en Python que integra las ventas de una tienda física (CSV), una tienda web (JSON) y un catálogo maestro de productos. Diagnostica la calidad de los datos, imputa nulos, trata outliers, integra y normaliza las fuentes, valida cada registro con **Pydantic** y persiste el resultado en **SQLite** mediante transacciones (`commit` / `rollback`).

Los registros inválidos no detienen el lote: se desvían a la tabla `cuarentena_errores` junto con el motivo del rechazo.

![Diagrama de flujo](docs/diagrama_flujo.png)

## Inicio rápido

Requiere Python 3.10 o superior.

```bash
pip install -r requirements.txt

python pipeline.py            # ejecuta el pipeline completo -> salida/
python generar_reporte.py     # reporte PDF -> salida/Reporte_Proyecto_Integrador_ETL.pdf
python generar_visor.py       # visor web de resultados -> salida/visor_datos.html
python test_estres.py         # batería de estrés (23 escenarios corruptos) -> salida_estres/
```

En Linux o macOS usa `python3` en lugar de `python`.

Pruebas unitarias:

```bash
python test_calidad.py
python test_integracion.py
python test_validacion_bd.py
```

### Opciones de `pipeline.py`

| Opción | Valor por defecto | Descripción |
|---|---|---|
| `--fisica` | `datasets/ventas_tienda_fisica.csv` | Ventas de la tienda física |
| `--online` | `datasets/ventas_online.json` | Ventas de la tienda web |
| `--catalogo` | `datasets/catalogo_productos.csv` | Catálogo maestro de productos |
| `--salida` | `salida` | Carpeta de resultados |
| `--tasa` | `19.50` | Tipo de cambio USD → MXN |
| `--factor-iqr` | `1.5` | Factor del rango intercuartílico para outliers |

Códigos de salida: `0` correcto · `1` terminó con errores controlados en alguna fuente o en la carga · `2` abortado de forma controlada.

## Datasets

La carpeta `datasets/` contiene los archivos de entrada: `catalogo_productos.csv`, `ventas_tienda_fisica.csv` y `ventas_online.json`.

Si los reemplazas por otra versión:

1. Ejecuta `python pipeline.py` y lee las **advertencias**: indican qué columnas se renombraron y cuáles no se reconocieron. Si algún nombre no se reconoce, agrégalo en `config.py` (`ALIAS_COLUMNAS` para ventas, `ALIAS_CATALOGO` para el catálogo).
2. Revisa los supuestos declarados en `REGLAS_DE_NEGOCIO.md` (sección 5) contra tus datos.
3. Vuelve a ejecutar `test_estres.py`, `generar_reporte.py` y `generar_visor.py`.

`generar_datos_demo.py` genera datos sintéticos en `datasets_demo/` sin tocar `datasets/`. Si los usas como entrada, el reporte se marca como "datos sintéticos".

## Estructura

```
config.py              parámetros (factor IQR, tasa de cambio, alias de columnas, tokens de nulo...)
calidad.py             fases 1-3: diagnóstico, nulos y outliers
integracion.py         fases 4-5: integración multifuente y normalización
validacion.py          fase 6: esquema Pydantic y cuarentena
base_datos.py          fase 7: carga transaccional a SQLite
pipeline.py            orquestador (línea de comandos)
test_*.py              pruebas unitarias y de estrés
generar_reporte.py     reporte PDF con los resultados reales
generar_visor.py       visor web de resultados
generar_diagrama.py    diagrama de flujo -> docs/diagrama_flujo.png
generar_datos_demo.py  datos sintéticos para pruebas
datasets/              archivos de entrada
docs/                  diagrama de flujo
salida/                resultados de la última corrida
```

## Resultados (`salida/`)

La carpeta `salida/` se genera al ejecutar el pipeline y no se versiona, salvo el reporte PDF y `resumen_pipeline.json`, que se incluyen como evidencia de una corrida.

| Archivo | Contenido |
|---|---|
| `ventas_retail.db` | Base SQLite con las tablas `transacciones_limpias` y `cuarentena_errores` |
| `transacciones_limpias.csv`, `cuarentena_errores.csv` | Volcado de ambas tablas |
| `resumen_pipeline.json` | Estado, métricas de cada fase y reconciliación de filas |
| `resumen_motivos_cuarentena.csv` | Causas de cuarentena agrupadas |
| `integrado_pre_validacion.csv` | Datos integrados antes de la validación |
| `ing1/` | Perfiles antes/después, clasificación MCAR/MAR, umbrales IQR, bitácoras, descartados y reportes de calidad por fuente |
| `pipeline.log` | Bitácora de ejecución |
| `Reporte_Proyecto_Integrador_ETL.pdf` | Reporte final (con `generar_reporte.py`) |
| `visor_datos.html` | Página autocontenida, sin internet, para explorar tablas, cuarentena, calidad e integración (con `generar_visor.py`) |

## Decisiones clave

El detalle completo está en `REGLAS_DE_NEGOCIO.md`.

- **Mediana, no media**, para imputar: las variables son asimétricas a la derecha por bots y compras gigantes.
- **Capping por IQR (1.5)** en lugar de eliminar filas, calculado por moneda para no confundir USD con MXN. Solo se eliminan duplicados exactos y registros con `cantidad ≤ 0`.
- **Llaves y fechas nunca se imputan.** Un producto fuera del catálogo se marca como *huérfano* y se rechaza en la validación.
- **Carga transaccional:** una transacción por corrida, un `SAVEPOINT` por registro y `ROLLBACK` total ante una falla catastrófica. La carga es idempotente: ejecutarla dos veces no duplica filas.
- **Reconciliación:** `entrada = descartadas + limpias + cuarentena`, verificada en cada corrida.
