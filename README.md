# Proyecto Integrador — Pipeline ETL robusto con zona de cuarentena
**Grupo 1 (LCS2) · Retail & E-commerce omnicanal** · Ingeniería de Datos (ATD-2602) · TecNM Campus Colima · Ago-Dic 2026

Pipeline end-to-end en Python: ingiere CSV (tienda física) + JSON (tienda web) + catálogo maestro, diagnostica la calidad, imputa y trata outliers, integra y normaliza, valida cada registro con **Pydantic** y persiste en **SQLite** con transacciones (`commit` / `rollback`). Los registros inválidos van a la tabla `cuarentena_errores` con su motivo, sin detener el lote.

## Inicio rápido

```bash
pip install -r requirements.txt

python3 pipeline.py                  # Fases 1-7 completas  -> salida/
python3 test_estres.py               # batería de estrés (23 escenarios corruptos) -> salida_estres/
python3 generar_reporte.py --integrantes "Nombre 1;Nombre 2;Nombre 3"   # PDF -> salida/Reporte_Proyecto_Integrador_ETL.pdf
python3 generar_visor.py             # página web para ver los datos -> salida/visor_datos.html (doble clic)
```

Pruebas unitarias: `python3 test_calidad.py` · `python3 test_integracion.py` · `python3 test_validacion_bd.py`

## Datasets

`datasets/` contiene los **archivos oficiales** proporcionados por el profesor (`catalogo_productos.csv`, `ventas_tienda_fisica.csv`, `ventas_online.json`). Si los reemplazas por otra versión:

1. Corre `python3 pipeline.py` y lee las **advertencias**: te dicen qué columnas se renombraron y cuáles no se reconocieron. Si algún nombre no se reconoce, agrégalo en `config.py › ALIAS_COLUMNAS` (ventas) o `ALIAS_CATALOGO` (catálogo).
2. Revisa los supuestos de `REGLAS_DE_NEGOCIO.md §5` contra los datos.
3. Corre `test_estres.py`, `generar_reporte.py` y `generar_visor.py`.

`generar_datos_demo.py` genera datos sintéticos en `datasets_demo/` (no toca `datasets/`); si los usas como entrada, el reporte se marca como "datos sintéticos" por el archivo `_DATOS_DEMO.txt`.

## Roles (matriz de responsabilidades)

| Rol | Fases | Archivos | Entregable clave |
|---|---|---|---|
| Ingeniero/a 1 — Calidad, Nulos y Outliers | 1, 2, 3 | `calidad.py`, `test_calidad.py` | Funciones de limpieza + reporte comparativo antes/después |
| Ingeniero/a 2 — Transformación e Integración | 4, 5 | `integracion.py`, `test_integracion.py` | Módulo de integración multifuente y normalización |
| Ingeniero/a 3 — Validación, Resiliencia y BD | 6, 7 | `validacion.py`, `base_datos.py`, `test_validacion_bd.py`, `test_estres.py` | Esquemas Pydantic + carga a BD + pruebas de estrés |
| Equipo | — | `pipeline.py`, `config.py`, `REGLAS_DE_NEGOCIO.md`, `generar_reporte.py`, `generar_diagrama.py` | Pipeline end-to-end, diagrama y reporte |

> Cada integrante debe subir **sus propios commits** al repositorio Git del equipo (la guía pide evidenciar los roles en Git).

## Estructura

```
config.py              parámetros (IQR 1.5, tasa 19.50, alias de columnas, tokens de nulo...)
calidad.py             Fases 1-3  (Ing. 1)
integracion.py         Fases 4-5  (Ing. 2)
validacion.py          Fase 6     (Ing. 3)  esquema Pydantic + cuarentena
base_datos.py          Fase 7     (Ing. 3)  SQLite transaccional
pipeline.py            orquestador (CLI)
test_*.py              pruebas unitarias y de estrés
generar_reporte.py     reporte PDF con los resultados reales
generar_visor.py       visor web de resultados -> salida/visor_datos.html
generar_diagrama.py    diagrama de flujo -> docs/diagrama_flujo.png
generar_datos_demo.py  datos sintéticos para pruebas
datasets/              entradas (demo hasta que pongas las oficiales)
docs/                  diagrama de flujo y guía para la defensa oral
salida/                resultados de la última corrida
```

## Qué produce `pipeline.py` (carpeta `salida/`)

| Archivo | Contenido |
|---|---|
| `ventas_retail.db` | **Base SQLite** con `transacciones_limpias` y `cuarentena_errores` |
| `transacciones_limpias.csv`, `cuarentena_errores.csv` | Volcado de ambas tablas (evidencia) |
| `resumen_pipeline.json` | Estado, métricas de cada fase y reconciliación de filas |
| `ing1/` | Perfiles, clasificación MCAR/MAR, umbrales IQR, bitácoras, `descartados_ing1.csv` y reportes por fuente |
| `resumen_motivos_cuarentena.csv` | Causas de cuarentena agrupadas |
| `pipeline.log` | Bitácora de ejecución |
| `Reporte_Proyecto_Integrador_ETL.pdf` | Reporte final (con `generar_reporte.py`) |
| `visor_datos.html` | Página autocontenida (sin internet) para explorar tablas, cuarentena, calidad e integración (con `generar_visor.py`) |

Códigos de salida de `pipeline.py`: `0` correcto · `1` terminó con errores controlados en alguna fuente o carga · `2` abortado de forma controlada.

## Cronograma de la guía ↔ entregables

| Día | Entregable | Dónde está |
|---|---|---|
| 1 | `REGLAS_DE_NEGOCIO.md` con justificación firmada + diagrama de flujo | `REGLAS_DE_NEGOCIO.md` (firmar §5), `docs/diagrama_flujo.png` |
| 2 | Limpieza y cruce multifuente en Git | `calidad.py`, `integracion.py` |
| 3 | Módulo Pydantic y bifurcación a cuarentena | `validacion.py` |
| 4 | Pipeline end-to-end con BD y pruebas de estrés | `pipeline.py`, `base_datos.py`, `test_estres.py` |
| 5 | Repositorio Git + BD SQLite + Reporte PDF y defensa oral | `salida/ventas_retail.db`, `salida/Reporte_...pdf`, `docs/GUIA_DEFENSA_ORAL.md` |

## Decisiones clave (detalle en `REGLAS_DE_NEGOCIO.md`)

- **Mediana** (no media) para imputar: las variables son asimétricas a la derecha por bots y compras gigantes.
- **Capping IQR 1.5** en vez de eliminar filas; por moneda, para no confundir USD con MXN. Solo se borran duplicados exactos y `cantidad ≤ 0`.
- **Llaves y fechas nunca se imputan.** Un producto fuera del catálogo es un *huérfano*: se marca y se rechaza en la validación.
- **Carga transaccional:** una transacción por corrida, un `SAVEPOINT` por registro y `ROLLBACK` total ante falla catastrófica; la carga es idempotente.
- **Reconciliación:** `entrada = descartadas + limpias + cuarentena`, verificada en cada corrida.
