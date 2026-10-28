"""
integracion.py — Ingeniero/a 2: Transformación e Integración (Fases 4 y 5)

Recibe las fuentes YA limpias del Ing. 1 (calidad.limpiar_fuente) y:
    Fase 4:  fechas -> YYYY-MM-DD · USD -> MXN @ 19.50 · cruce con catálogo maestro · marca de huérfanos
    Fase 5:  escalamiento Min-Max [0,1] · discretización del importe en Bajo / Medio / Alto

Igual que el módulo del Ing. 1: nada se borra en silencio y no se lanzan excepciones por datos sucios.
Lo que no se puede interpretar (fecha imposible, llave huérfana) se MARCA; la decisión de enviarlo a
cuarentena la toma la validación Pydantic (Fase 6).
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import numpy as np
import pandas as pd

import calidad as cal
import config as cfg

# =============================================================================
# Fase 4a — Fechas a ISO (YYYY-MM-DD)
# =============================================================================

_RE_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?$")          # 2026-03-04 / 2026-03-04T10:22:11
_RE_YMD_SLASH = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")                # 2026/03/04
_RE_DMY = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})(?:[T ].*)?$")   # 04/03/2026 (día/mes/año)
_RE_EPOCH = re.compile(r"^(\d{10}|\d{13})(?:\.0+)?$")                                    # 1789105020 (s) / 1789105020000 (ms)


def _fecha_a_iso(valor):
    """Devuelve (iso | None, formato | motivo_de_fallo). Nunca lanza excepción."""
    if valor is None or (not isinstance(valor, str) and pd.isna(valor)):
        return None, "nula"
    t = str(valor).strip()
    if m := _RE_EPOCH.match(t):
        seg = int(m.group(1)) / (1000 if len(m.group(1)) == 13 else 1)
        zona = dt.timezone(dt.timedelta(hours=cfg.HORAS_UTC_EPOCH))
        try:
            return dt.datetime.fromtimestamp(seg, zona).date().isoformat(), "EPOCH"
        except (OverflowError, OSError, ValueError):
            return None, "fecha_imposible"
    if m := _RE_ISO.match(t):
        a, mes, d = m.groups()
        formato = "ISO"
    elif m := _RE_YMD_SLASH.match(t):
        a, mes, d = m.groups()
        formato = "AAAA/MM/DD"
    elif m := _RE_DMY.match(t):
        d, mes, a = m.groups()
        a = f"20{a}" if len(a) == 2 else a
        formato = "DMY"
    else:
        return None, "formato_no_reconocido"
    try:
        f = dt.date(int(a), int(mes), int(d))
    except ValueError:
        return None, "fecha_imposible"          # 31/02/2026, mes 13, año 0…
    return f.isoformat(), formato


def normalizar_fechas(df: pd.DataFrame, col: str = "fecha"):
    """
    Homologa `col` a 'YYYY-MM-DD' (texto). Conserva el valor original en `fecha_original`.
    Convención para fechas con '/' o '-' al inicio día: DÍA/MES/AÑO (formato mexicano, según la guía).
    Devuelve (df, stats).
    """
    df = df.copy()
    stats = {"filas": int(len(df)), "iso": 0, "epoch": 0, "dmy": 0, "otro_formato_valido": 0, "nulas": 0,
             "imposibles": 0, "formato_no_reconocido": 0, "dmy_ambiguas_dia_y_mes_le_12": 0}
    if col not in df.columns:
        stats["advertencia"] = f"No existe la columna '{col}'."
        return df, stats

    df["fecha_original"] = df[col]
    unicos = {v: _fecha_a_iso(v) for v in pd.unique(df[col])}      # cada valor distinto se interpreta una vez
    iso = df[col].map(lambda v: unicos[v][0])
    formato = df[col].map(lambda v: unicos[v][1])

    m_dmy = formato == "DMY"
    amb = df.loc[m_dmy, col].astype("string").str.extract(r"^(\d{1,2})[/\-.](\d{1,2})")
    stats["dmy_ambiguas_dia_y_mes_le_12"] = int(((amb[0].astype(float) <= 12) & (amb[1].astype(float) <= 12)).sum())

    stats["iso"] = int((formato == "ISO").sum())
    stats["epoch"] = int((formato == "EPOCH").sum())
    stats["dmy"] = int(m_dmy.sum())
    stats["otro_formato_valido"] = int((formato == "AAAA/MM/DD").sum())
    stats["nulas"] = int((formato == "nula").sum())
    stats["imposibles"] = int((formato == "fecha_imposible").sum())
    stats["formato_no_reconocido"] = int((formato == "formato_no_reconocido").sum())

    df[col] = iso.astype("string")
    cal._marcar(df, m_dmy | formato.isin(["AAAA/MM/DD", "EPOCH"]), "fecha:convertida_a_ISO")
    cal._marcar(df, formato.isin(["fecha_imposible", "formato_no_reconocido"]), "fecha:invalida_a_nulo")
    return df, stats


# =============================================================================
# Fase 4b — Conversión USD -> MXN
# =============================================================================


def convertir_a_mxn(df: pd.DataFrame, tasa: float = cfg.TASA_USD_MXN):
    """
    Convierte precio_unitario y monto_total de las filas en USD a MXN con la paridad oficial.
    Conserva `moneda_original`. Monedas distintas de MXN/USD se dejan INTACTAS (Pydantic las rechazará).
    """
    df = df.copy()
    stats = {"tasa_usd_mxn": tasa, "filas_usd_convertidas": 0, "monedas_no_reconocidas": 0}
    if "moneda" not in df.columns:
        stats["advertencia"] = "No hay columna 'moneda': no se convirtió nada."
        return df, stats
    df["moneda_original"] = df["moneda"]
    usd = df["moneda"] == "USD"
    for c in ("precio_unitario", "monto_total"):
        if c in df.columns:
            df.loc[usd, c] = (pd.to_numeric(df.loc[usd, c], errors="coerce") * tasa).round(2)
    df.loc[usd, "moneda"] = "MXN"
    stats["filas_usd_convertidas"] = cal._marcar(df, usd, f"moneda:USD_a_MXN@{tasa:.2f}")
    stats["monedas_no_reconocidas"] = int((~df["moneda"].isin(["MXN", "USD"]) & df["moneda"].notna()).sum())
    return df, stats


# =============================================================================
# Fase 4c — Catálogo maestro, cruce y huérfanos
# =============================================================================


def preparar_catalogo(df_cat: pd.DataFrame):
    """Homologa columnas del catálogo, limpia la llave y elimina llaves duplicadas/nulas (se reportan)."""
    inverso = {}
    for canon, alias in cfg.ALIAS_CATALOGO.items():
        for a in alias + [canon]:
            inverso[cal._slug(a)] = canon
    usados, ren = set(), {}
    for c in df_cat.columns:
        base = cal._slug(c)
        destino = inverso.get(base, base)
        if destino in usados:
            destino = base
        usados.add(destino)
        ren[c] = destino
    cat = df_cat.rename(columns=ren).copy()
    stats = {"registros_entrada": int(len(cat)), "ids_nulos": 0, "ids_duplicados": 0}
    if "id_producto" not in cat.columns:
        raise ValueError("El catálogo no tiene una columna de llave reconocible (id_producto / sku / codigo).")
    cat["id_producto"] = cat["id_producto"].astype("string").str.strip().str.upper()
    cat["id_producto"] = cat["id_producto"].mask(cat["id_producto"].str.lower().isin(list(cfg.TOKENS_NULOS)))
    stats["ids_nulos"] = int(cat["id_producto"].isna().sum())
    cat = cat[cat["id_producto"].notna()]
    stats["ids_duplicados"] = int(cat["id_producto"].duplicated().sum())
    cat = cat.drop_duplicates(subset="id_producto", keep="first")
    for c in ("nombre_producto", "categoria"):
        if c in cat.columns:
            cat[c] = cat[c].astype("string").str.strip().str.upper()
    if "precio_lista" in cat.columns:
        cat["precio_lista"] = cal.a_numero(cat["precio_lista"])
    cols = [c for c in ["id_producto", "nombre_producto", "categoria", "precio_lista"] if c in cat.columns]
    stats["registros_salida"] = int(len(cat))
    return cat[cols].reset_index(drop=True), stats


def cargar_catalogo(ruta):
    return preparar_catalogo(cal.cargar_fuente(ruta))


def cruzar_catalogo(df: pd.DataFrame, catalogo: pd.DataFrame):
    """
    Left-join contra el catálogo por id_producto. Agrega nombre_producto / categoria / precio_lista y
    `es_huerfano` (True si el producto no existe en el catálogo o si la llave viene nula).
    No elimina nada: los huérfanos se desvían a cuarentena en la Fase 6.
    """
    df = df.copy()
    stats = {"filas": int(len(df)), "huerfanos": 0, "huerfanos_llave_nula": 0, "productos_distintos_huerfanos": 0}
    if "id_producto" not in df.columns:
        df["es_huerfano"] = True
        stats.update(huerfanos=int(len(df)), advertencia="No hay columna id_producto: todo se marca huérfano.")
        return df, stats
    cat = catalogo.copy()
    cat["_en_catalogo"] = True
    df = df.merge(cat, on="id_producto", how="left", validate="m:1")
    df["es_huerfano"] = df["_en_catalogo"].isna()
    df = df.drop(columns="_en_catalogo")
    cal._marcar(df, df["es_huerfano"], "id_producto:huerfano")
    stats["huerfanos"] = int(df["es_huerfano"].sum())
    stats["huerfanos_llave_nula"] = int((df["es_huerfano"] & df["id_producto"].isna()).sum())
    stats["productos_distintos_huerfanos"] = int(df.loc[df["es_huerfano"], "id_producto"].nunique())
    return df, stats


# =============================================================================
# Unificación de fuentes
# =============================================================================


def unificar_fuentes(fuentes: list[pd.DataFrame]) -> pd.DataFrame:
    """Apila las fuentes (ya homologadas por Ing. 1) conservando todas las columnas."""
    fuentes = [f for f in fuentes if f is not None and len(f.columns)]
    if not fuentes:
        return pd.DataFrame()
    return pd.concat(fuentes, ignore_index=True, sort=False)


# =============================================================================
# Fase 5 — Min-Max y discretización
# =============================================================================


def escalar_minmax(df: pd.DataFrame, col: str = "monto_total", salida: str | None = None):
    """x' = (x - min) / (max - min)  en [0, 1]. Si max == min (sin variación) todo queda en 0.0."""
    df = df.copy()
    salida = salida or f"{col}_minmax"
    if col not in df.columns:
        df[salida] = np.nan
        return df, {"advertencia": f"No existe '{col}'."}
    v = pd.to_numeric(df[col], errors="coerce")
    lo, hi = v.min(), v.max()
    if pd.isna(lo):
        df[salida] = np.nan
    elif hi == lo:
        df[salida] = np.where(v.notna(), 0.0, np.nan)
    else:
        df[salida] = ((v - lo) / (hi - lo)).round(6)
    return df, {"columna": col, "minimo": None if pd.isna(lo) else float(lo), "maximo": None if pd.isna(hi) else float(hi)}


def discretizar_monto(df: pd.DataFrame, col: str = "monto_total", salida: str = "segmento_monto", cortes=None):
    """
    3 segmentos: Bajo / Medio / Alto.
      · Por defecto: TERCILES del importe en MXN (cada segmento ~33 % de las ventas; robusto a la asimetría).
      · `cortes=(a, b)`: reglas de negocio fijas (Bajo <= a < Medio <= b < Alto).
    """
    df = df.copy()
    v = pd.to_numeric(df[col], errors="coerce") if col in df.columns else pd.Series(np.nan, index=df.index)
    if cortes is None:
        a, b = (float(v.quantile(q)) if v.notna().any() else np.nan for q in cfg.CUANTILES_SEGMENTO)
        metodo = "terciles (cuantiles 33.3 % y 66.7 %)"
    else:
        a, b = cortes
        metodo = "reglas de negocio"
    etiquetas = cfg.ETIQUETAS_SEGMENTO
    seg = np.select([v <= a, v <= b, v > b], etiquetas, default="")
    df[salida] = pd.Series(seg, index=df.index).replace("", pd.NA).astype("string")
    stats = {"metodo": metodo, "corte_bajo_medio": a, "corte_medio_alto": b,
             "conteo": {e: int((df[salida] == e).sum()) for e in etiquetas}}
    return df, stats


# =============================================================================
# Atajo: Fases 4 y 5 completas
# =============================================================================


def integrar(fuentes_limpias: list[pd.DataFrame], catalogo: pd.DataFrame, tasa: float = cfg.TASA_USD_MXN):
    """Fases 4 y 5 sobre las fuentes del Ing. 1. Devuelve (df_integrado, stats por paso)."""
    stats = {}
    df = unificar_fuentes(fuentes_limpias)
    stats["filas_unificadas"] = int(len(df))
    if df.empty:
        return df, stats
    df, stats["fechas"] = normalizar_fechas(df)
    df, stats["moneda"] = convertir_a_mxn(df, tasa)
    df, stats["catalogo"] = cruzar_catalogo(df, catalogo)
    df, stats["minmax"] = escalar_minmax(df, "monto_total")
    df, stats["segmentos"] = discretizar_monto(df, "monto_total")
    if "flags_ing1" in df.columns:
        df["flags_ing1"] = df["flags_ing1"].fillna("").astype("string").str.rstrip("|")
    return df, stats
