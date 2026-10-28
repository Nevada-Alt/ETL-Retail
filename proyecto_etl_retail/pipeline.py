"""
pipeline.py — Orquestador del Pipeline ETL robusto con zona de cuarentena (Grupo 1: Retail & E-commerce)

    python3 pipeline.py                                   # usa datasets/ y escribe en salida/
    python3 pipeline.py --fisica a.csv --online b.json --catalogo c.csv --salida out

Fases:
    1 Diagnóstico de calidad (Ing.1)        5 Min-Max y discretización (Ing.2)
    2 Faltantes e imputación (Ing.1)        6 Validación Pydantic + cuarentena (Ing.3)
    3 Outliers IQR / capping (Ing.1)        7 Carga transaccional SQLite (Ing.3)
    4 Integración, fechas, USD->MXN, catálogo y huérfanos (Ing.2)

Códigos de salida: 0 = todo bien · 1 = terminó pero con errores en alguna fuente / carga · 2 = abortado de forma controlada.
El pipeline NO lanza excepciones por datos corruptos: cada falla queda en `resumen_pipeline.json`.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import traceback
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

import base_datos as bd
import calidad as cal
import config as cfg
import integracion as ing
import validacion as val


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    return str(o)


def _logger(salida: Path) -> logging.Logger:
    log = logging.getLogger(f"etl.{salida.resolve()}")
    log.setLevel(logging.INFO)
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S")
    fh = logging.FileHandler(salida / "pipeline.log", mode="w", encoding="utf-8")
    sh = logging.StreamHandler(sys.stdout)
    for h in (fh, sh):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.propagate = False
    return log


def _procesar_fuente(nombre: str, ruta, salida: Path, factor_iqr: float, log: logging.Logger):
    """Fases 1-3 sobre una fuente. Devuelve (df_limpio, info)."""
    log.info(f"[{nombre}] Cargando {ruta}")
    crudo = cal.cargar_fuente(ruta)
    raw, mapa = cal.homologar_columnas(crudo)
    renombradas = {k: v for k, v in mapa.items() if k != v}
    log.info(f"[{nombre}] {len(raw)} filas, {len(raw.columns)} columnas. Renombradas: {renombradas}")

    res_a, perfil_a = cal.perfilar_calidad(raw, nombre)                                  # Fase 1
    falt = cal.clasificar_faltantes(raw)                                                 # Fase 2 (diagnóstico)
    limpio, desc, bit, umbrales = cal.limpiar_fuente(raw, nombre, factor_iqr=factor_iqr)  # Fases 2 y 3
    res_d, perfil_d = cal.perfilar_calidad(limpio.drop(columns=["fila_origen", "fuente", "flags_ing1"]), nombre)
    tablas = cal.comparar_antes_despues(raw, limpio, res_a, perfil_a, res_d, perfil_d, umbrales, bit)
    cal.guardar_reporte(salida / "ing1", nombre, raw, limpio, desc, bit, umbrales, perfil_a, perfil_d,
                        res_a, res_d, falt, tablas)
    for a in bit["advertencias"]:
        log.warning(f"[{nombre}] {a}")
    log.info(f"[{nombre}] Fases 1-3: {bit['filas_entrada']} -> {bit['filas_salida']} filas "
             f"({bit['filas_descartadas']} descartadas con motivo)")
    info = {"ruta": str(ruta), "columnas_renombradas": renombradas, "perfil_antes": res_a, "perfil_despues": res_d,
            "bitacora": bit, "faltantes_clasificacion": falt.to_dict("records")}
    return limpio, info


def ejecutar_pipeline(fisica, online, catalogo, salida="salida", nombre_bd=cfg.NOMBRE_BD,
                      tasa: float = cfg.TASA_USD_MXN, factor_iqr: float = cfg.FACTOR_IQR) -> dict:
    # Valores absurdos (1e308, inf) provocan avisos de overflow de numpy: ya se manejan como datos inválidos.
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)
        return _ejecutar(fisica, online, catalogo, salida, nombre_bd, tasa, factor_iqr)


def _ejecutar(fisica, online, catalogo, salida, nombre_bd, tasa, factor_iqr) -> dict:
    t0 = time.time()
    salida = Path(salida)
    salida.mkdir(parents=True, exist_ok=True)
    log = _logger(salida)
    resumen = {"estado": "OK", "errores": [], "advertencias": [], "fuentes": {},
               "datos_demo": (Path(fisica).parent / "_DATOS_DEMO.txt").exists(),
               "parametros": {"tasa_usd_mxn": tasa, "factor_iqr": factor_iqr}}
    log.info("=== PIPELINE ETL — Retail & E-commerce ===")
    if resumen["datos_demo"]:
        resumen["advertencias"].append("Se están usando datos SINTÉTICOS de demostración (datasets/_DATOS_DEMO.txt).")
        log.warning("Datos SINTÉTICOS de demostración detectados.")

    # ------------------------------------------------------------------ catálogo maestro
    try:
        catalogo_df, resumen["catalogo"] = ing.cargar_catalogo(catalogo)
        log.info(f"[catalogo] {resumen['catalogo']}")
    except Exception as e:      # noqa: BLE001
        resumen["estado"] = "ABORTADO"
        resumen["errores"].append(f"catalogo: {type(e).__name__}: {e}")
        log.error(f"No se pudo cargar el catálogo maestro: {e}. Sin catálogo no se pueden validar llaves; se aborta.")
        (salida / "resumen_pipeline.json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
        return resumen

    # ------------------------------------------------------------------ Fases 1-3 por fuente
    limpias = []
    for nombre, ruta in (("fisica", fisica), ("online", online)):
        try:
            limpio, info = _procesar_fuente(nombre, ruta, salida, factor_iqr, log)
            limpias.append(limpio)
            resumen["fuentes"][nombre] = info
        except Exception as e:  # noqa: BLE001 — una fuente rota no tumba a la otra
            resumen["errores"].append(f"{nombre}: {type(e).__name__}: {e}")
            resumen["fuentes"][nombre] = {"ruta": str(ruta), "error": f"{type(e).__name__}: {e}"}
            log.error(f"[{nombre}] No se pudo procesar: {type(e).__name__}: {e}")
            log.debug(traceback.format_exc())
    if not limpias:
        resumen["estado"] = "ABORTADO"
        log.error("Ninguna fuente pudo procesarse. Fin controlado.")
        (salida / "resumen_pipeline.json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
        return resumen

    # ------------------------------------------------------------------ Fases 4-5
    df, resumen["integracion"] = ing.integrar(limpias, catalogo_df, tasa)
    s = resumen["integracion"]
    log.info(f"[Fase 4] fechas={s.get('fechas')}")
    log.info(f"[Fase 4] moneda={s.get('moneda')} · catalogo={s.get('catalogo')}")
    log.info(f"[Fase 5] minmax={s.get('minmax')} · segmentos={s.get('segmentos')}")
    df.to_csv(salida / "integrado_pre_validacion.csv", index=False, encoding="utf-8")

    # ------------------------------------------------------------------ Fase 6
    df_ok, df_bad, resumen["validacion"] = val.validar_registros(df)
    motivos = val.resumen_motivos(df_bad)
    motivos.to_csv(salida / "resumen_motivos_cuarentena.csv", index=False, encoding="utf-8")
    resumen["validacion"]["motivos_top"] = motivos.head(15).to_dict("records")
    log.info(f"[Fase 6] {resumen['validacion']['validos']} válidos · {resumen['validacion']['cuarentena']} a cuarentena")

    # ------------------------------------------------------------------ Fase 7
    ruta_bd = salida / nombre_bd
    conn = None
    try:
        conn = bd.conectar(ruta_bd)
        resumen["carga"] = bd.cargar(conn, df_ok, df_bad)
        resumen["bd"] = bd.verificar(conn)
        bd.exportar_tablas_csv(conn, salida)
        log.info(f"[Fase 7] COMMIT · {resumen['bd']['filas_transacciones_limpias']} limpias · "
                 f"{resumen['bd']['filas_cuarentena_errores']} en cuarentena · integrity={resumen['bd']['integrity_check']}")
    except bd.CargaFallida as e:
        resumen["estado"] = "CARGA_REVERTIDA"
        resumen["errores"].append(f"carga: {e}")
        log.error(f"[Fase 7] {e}")
    except Exception as e:      # noqa: BLE001
        resumen["estado"] = "CARGA_REVERTIDA"
        resumen["errores"].append(f"carga: {type(e).__name__}: {e}")
        log.error(f"[Fase 7] Falla inesperada de BD: {type(e).__name__}: {e}")
    finally:
        if conn is not None:
            conn.close()

    # ------------------------------------------------------------------ reconciliación: ¿se perdió algún registro?
    entrada = sum(i["bitacora"]["filas_entrada"] for i in resumen["fuentes"].values() if "bitacora" in i)
    descartados = sum(i["bitacora"]["filas_descartadas"] for i in resumen["fuentes"].values() if "bitacora" in i)
    if "bd" in resumen:
        en_bd = resumen["bd"]["filas_transacciones_limpias"] + resumen["bd"]["filas_cuarentena_errores"]
        resumen["reconciliacion"] = {"filas_entrada": entrada, "descartadas_ing1": descartados,
                                     "en_transacciones_limpias": resumen["bd"]["filas_transacciones_limpias"],
                                     "en_cuarentena_errores": resumen["bd"]["filas_cuarentena_errores"],
                                     "cuadra": bool(entrada == descartados + en_bd)}
        log.info(f"[Reconciliación] entrada={entrada} = descartadas {descartados} + limpias "
                 f"{resumen['bd']['filas_transacciones_limpias']} + cuarentena {resumen['bd']['filas_cuarentena_errores']} "
                 f"-> {'OK' if resumen['reconciliacion']['cuadra'] else 'NO CUADRA'}")
        if not resumen["reconciliacion"]["cuadra"]:
            resumen["errores"].append("La reconciliación de filas no cuadra.")

    if resumen["estado"] == "OK" and resumen["errores"]:
        resumen["estado"] = "PARCIAL"
    resumen["duracion_segundos"] = round(time.time() - t0, 2)
    (salida / "resumen_pipeline.json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
    log.info(f"=== FIN · estado={resumen['estado']} · {resumen['duracion_segundos']} s ===")
    for h in list(log.handlers):
        h.close()
        log.removeHandler(h)
    return resumen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Pipeline ETL robusto con zona de cuarentena")
    ap.add_argument("--fisica", default="datasets/ventas_tienda_fisica.csv")
    ap.add_argument("--online", default="datasets/ventas_online.json")
    ap.add_argument("--catalogo", default="datasets/catalogo_productos.csv")
    ap.add_argument("--salida", default="salida")
    ap.add_argument("--tasa", type=float, default=cfg.TASA_USD_MXN)
    ap.add_argument("--factor-iqr", type=float, default=cfg.FACTOR_IQR)
    a = ap.parse_args(argv)
    r = ejecutar_pipeline(a.fisica, a.online, a.catalogo, a.salida, tasa=a.tasa, factor_iqr=a.factor_iqr)
    return {"OK": 0, "PARCIAL": 1, "CARGA_REVERTIDA": 1, "ABORTADO": 2}.get(r["estado"], 1)


if __name__ == "__main__":
    sys.exit(main())
