"""
calidad.py — Ingeniero/a 1: Calidad, Nulos y Outliers  (Fases 1, 2 y 3 del pipeline)

Flujo recomendado (lo orquesta run_ing1.py):

    df_raw            = homologar_columnas(cargar_fuente(ruta))[0]
    resumen, perfil   = perfilar_calidad(df_raw, "fisica")             # Fase 1
    faltantes         = clasificar_faltantes(df_raw)                   # Fase 2 (MCAR / MAR)
    df, desc, bit, um = limpiar_fuente(df_raw, "fisica")               # Fases 2 y 3
    tablas            = comparar_antes_despues(...)                    # Reporte antes/después

Principios:
  * Nada se borra en silencio: lo descartado va a `descartados` con su motivo.
  * Cada imputación / recálculo / capping queda marcado en la columna `flags_ing1`.
  * Las fechas NO se tocan (texto crudo) y la moneda NO se convierte: eso es Fase 4 (Ing. 2).
  * Las funciones no lanzan excepciones por datos sucios: ante columnas faltantes
    avisan en `bitacora["advertencias"]` y continúan.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

import config as cfg

# =============================================================================
# 0. Utilidades de carga y normalización de nombres
# =============================================================================


def _es_texto(s: pd.Series) -> bool:
    return pd.api.types.is_string_dtype(s) or pd.api.types.is_object_dtype(s)


def _slug(nombre) -> str:
    """'Fecha Venta ' / 'FECHA_VENTA' / 'Fecha-Venta' -> 'fecha_venta' (sin acentos)."""
    t = unicodedata.normalize("NFKD", str(nombre)).encode("ascii", "ignore").decode()
    return re.sub(r"[^0-9a-zA-Z]+", "_", t.strip().lower()).strip("_")


def _hashable(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte listas/dicts (típicos de JSON anidado) a texto para poder detectar duplicados."""
    return df.map(lambda v: json.dumps(v, sort_keys=True, default=str) if isinstance(v, (list, dict)) else v)


def cargar_fuente(ruta) -> pd.DataFrame:
    """
    Lee CSV, JSON (lista de objetos, objeto con una lista dentro, o JSON Lines).
    Se lee SIN limpiar nada: los textos como 'N/A' se conservan para poder medirlos en el perfilado.
    """
    ruta = Path(ruta)
    ext = ruta.suffix.lower()
    if ext in (".csv", ".txt", ".tsv"):
        sep = "\t" if ext == ".tsv" else None
        ultimo_error = None
        for enc in ("utf-8-sig", "latin-1"):
            try:
                return pd.read_csv(
                    ruta, encoding=enc, sep=sep, engine="python",
                    keep_default_na=False, na_values=[""], skipinitialspace=True,
                    on_bad_lines="warn",
                )
            except UnicodeDecodeError as e:   # probar siguiente codificación
                ultimo_error = e
        raise ultimo_error  # pragma: no cover
    if ext in (".json", ".jsonl", ".ndjson"):
        try:
            with open(ruta, encoding="utf-8-sig") as f:
                datos = json.load(f)
        except json.JSONDecodeError:                      # ¿JSON Lines? (un objeto por línea)
            registros, invalidas = [], 0
            with open(ruta, encoding="utf-8-sig") as f:
                for linea in f:
                    if not linea.strip():
                        continue
                    try:
                        registros.append(json.loads(linea))
                    except json.JSONDecodeError:
                        invalidas += 1
            if not registros:
                raise ValueError("El archivo no es JSON ni JSON Lines válido.")
            df = pd.json_normalize([r for r in registros if isinstance(r, dict)], sep="_")
            df.attrs["lineas_json_invalidas"] = invalidas
            return df
        return pd.json_normalize(_extraer_registros(datos), sep="_")
    raise ValueError(f"Formato no soportado: {ext}")


def _extraer_registros(datos):
    if isinstance(datos, list):
        return datos
    if isinstance(datos, dict):
        for v in datos.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        return [datos]
    return []


def homologar_columnas(df: pd.DataFrame, mapa_manual: dict | None = None):
    """
    Renombra columnas al esquema canónico usando cfg.ALIAS_COLUMNAS.
    Devuelve (df_renombrado, mapa_aplicado {original: nuevo}).
    """
    inverso = {}
    for canon, alias in cfg.ALIAS_COLUMNAS.items():
        for a in alias + [canon]:
            inverso[_slug(a)] = canon
    for k, v in (mapa_manual or {}).items():
        inverso[_slug(k)] = v

    usados, aplicado = set(), {}
    for c in df.columns:
        base = _slug(c)
        destino = inverso.get(base, base)
        if destino in usados:      # dos columnas apuntan al mismo canónico: la segunda conserva su nombre
            destino = base
        if destino in usados:
            destino = f"{base}_dup"
        usados.add(destino)
        aplicado[c] = destino
    return df.rename(columns=aplicado), aplicado


# =============================================================================
# 1. Conversión numérica tolerante
# =============================================================================


def a_numero(s: pd.Series) -> pd.Series:
    """
    Convierte a float lo que "parece" número: '$1,234.50', '1234.5 MXN', '12,50', ' 7 '.
    Lo que no se puede convertir queda como NaN (no lanza excepción).
    """
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        out = pd.to_numeric(s, errors="coerce").astype("float64")
        return out.replace([np.inf, -np.inf], np.nan)
    t = s.astype("string").str.strip()
    t = t.str.replace(r"(?i)(mxn|usd|mn|dlls?|dls)", "", regex=True)
    t = t.str.replace(r"[\$\s]", "", regex=True)
    europeo = t.str.match(r"^-?\d{1,3}(\.\d{3})*,\d{1,2}$").fillna(False).astype(bool)   # 1.234,50 / 12,50
    t = t.mask(europeo, t.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
    t = t.str.replace(",", "", regex=False)                                                # 1,234.50
    out = pd.to_numeric(t, errors="coerce").astype("float64")
    return out.replace([np.inf, -np.inf], np.nan)


def _mascara_pseudonulos(s: pd.Series) -> pd.Series:
    """True donde el valor NO es NaN pero es texto de 'dato faltante' ('N/A', '-', 's/d', ...)."""
    if not _es_texto(s):
        return pd.Series(False, index=s.index)
    t = s.astype("string").str.strip().str.lower()
    return (t.isin(list(cfg.TOKENS_NULOS)).fillna(False).astype(bool)) & s.notna()


def _fraccion_numerica(s: pd.Series) -> float:
    validos = s[s.notna() & ~_mascara_pseudonulos(s)]
    if len(validos) == 0:
        return 0.0
    return float(a_numero(validos).notna().mean())


_RE_ISO = r"^\d{4}-\d{2}-\d{2}"
_RE_DMY = r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"


def _perfil_fechas(s: pd.Series):
    """Si la columna parece fecha en texto, devuelve (True, 'ISO: n | D/M/A: n | otros: n')."""
    t = s.dropna().astype("string").str.strip()
    if len(t) == 0:
        return False, ""
    iso = int(t.str.match(_RE_ISO).sum())
    dmy = int(t.str.match(_RE_DMY).sum())
    if (iso + dmy) / len(t) < 0.5:
        return False, ""
    return True, f"ISO: {iso} | D/M/A: {dmy} | otros: {len(t) - iso - dmy}"


# =============================================================================
# 2. FASE 1 — Diagnóstico de calidad inicial
# =============================================================================


def perfilar_calidad(df: pd.DataFrame, nombre: str = "fuente"):
    """
    Perfilado cuantitativo ANTES de transformar nada.
    Devuelve (resumen: dict, perfil: DataFrame por columna).
      - completitud: % de valores presentes (cuenta NaN y también pseudo-nulos como 'N/A')
      - unicidad: duplicados exactos de fila + duplicados de llave id_venta
      - tipos: dtype real vs. tipo inferido (ej. 'numerico_como_texto')
    """
    n = len(df)
    hdf = _hashable(df)
    filas = []
    for c in df.columns:
        s = df[c]
        nulos = int(s.isna().sum())
        pseudo = int(_mascara_pseudonulos(s).sum())
        faltantes = nulos + pseudo
        if pd.api.types.is_bool_dtype(s):
            tipo = "booleano"
            nota = ""
        elif pd.api.types.is_numeric_dtype(s):
            tipo = "numerico"
            nota = ""
        elif _es_texto(s):
            nota = ""
            es_fecha, nota_f = _perfil_fechas(s)
            if _fraccion_numerica(s) >= 0.9:
                tipo = "numerico_como_texto"
            elif es_fecha:
                tipo, nota = "fecha_como_texto", nota_f
            else:
                tipo = "texto"
        else:
            tipo, nota = str(s.dtype), ""
        ejemplo = s.dropna()
        filas.append({
            "columna": c,
            "dtype": str(s.dtype),
            "tipo_inferido": tipo,
            "nulos": nulos,
            "pseudo_nulos": pseudo,
            "faltantes_total": faltantes,
            "pct_faltantes": round(100 * faltantes / n, 2) if n else 0.0,
            "completitud_pct": round(100 * (1 - faltantes / n), 2) if n else 100.0,
            "unicos": int(hdf[c].nunique(dropna=True)),
            "ejemplo": ejemplo.iloc[0] if len(ejemplo) else None,
            "nota": nota,
        })
    perfil = pd.DataFrame(filas)

    dup_exactos = int(hdf.duplicated().sum()) if n else 0
    dup_llave = 0
    if "id_venta" in df.columns and n:
        llave = df["id_venta"].dropna()
        dup_llave = int(llave.duplicated().sum())
    total_celdas = n * len(df.columns)
    faltantes_celdas = int(perfil["faltantes_total"].sum()) if len(perfil) else 0
    mask_fila = pd.Series(False, index=df.index)
    for c in df.columns:
        mask_fila |= df[c].isna() | _mascara_pseudonulos(df[c])

    resumen = {
        "fuente": nombre,
        "filas": n,
        "columnas": len(df.columns),
        "duplicados_exactos": dup_exactos,
        "pct_duplicados_exactos": round(100 * dup_exactos / n, 2) if n else 0.0,
        "duplicados_id_venta": dup_llave,
        "filas_con_algun_faltante": int(mask_fila.sum()),
        "completitud_global_pct": round(100 * (1 - faltantes_celdas / total_celdas), 2) if total_celdas else 100.0,
    }
    return resumen, perfil


# =============================================================================
# 3. FASE 2 — Clasificación de faltantes (MCAR / MAR)
# =============================================================================


def _preparar_para_pruebas(df: pd.DataFrame):
    numericas, categoricas, faltante = {}, {}, {}
    for c in df.columns:
        s = df[c]
        pn = _mascara_pseudonulos(s)
        faltante[c] = s.isna() | pn
        s2 = s.mask(pn)
        if pd.api.types.is_numeric_dtype(s2) and not pd.api.types.is_bool_dtype(s2):
            numericas[c] = pd.to_numeric(s2, errors="coerce")
        elif _es_texto(s2):
            if _fraccion_numerica(s2) >= 0.8:
                numericas[c] = a_numero(s2)
            else:
                categoricas[c] = s2.astype("string").str.strip().str.upper()
    return numericas, categoricas, faltante


def clasificar_faltantes(df: pd.DataFrame, alfa: float = cfg.ALFA, min_faltantes: int = 3) -> pd.DataFrame:
    """
    Clasifica el mecanismo de ausencia de cada columna con faltantes.

    Método: para cada columna X con faltantes se crea el indicador M = (X está ausente) y se prueba si M
    se asocia con el resto de columnas observadas:
        · columna numérica  -> Mann-Whitney U (distribución de la variable con M=1 vs M=0)
        · columna categórica (<= 20 niveles) -> Chi-cuadrada de independencia
    Se aplica corrección de Bonferroni sobre el número de pruebas.
        · p_ajustado < alfa   -> MAR  (la ausencia depende de otra variable observada)
        · p_ajustado >= alfa  -> MCAR (sin evidencia de dependencia; compatible con azar)
    Limitación (decirlo en el reporte): MNAR no puede verificarse con los datos observados;
    se descarta por conocimiento del dominio, no por una prueba.
    """
    num, cat, falt = _preparar_para_pruebas(df)
    n = len(df)
    filas = []
    for c in df.columns:
        m = falt[c]
        k = int(m.sum())
        if k == 0:
            continue
        # ¿cuántos faltantes son recalculables a partir de otras columnas?
        recalculables = 0
        if c in cfg.COLUMNAS_NUMERICAS and all(x in num for x in cfg.COLUMNAS_NUMERICAS):
            otras = [x for x in cfg.COLUMNAS_NUMERICAS if x != c]
            recalculables = int((m & ~falt[otras[0]] & ~falt[otras[1]]).sum())

        resultados = []
        if k >= min_faltantes and (n - k) >= min_faltantes:
            for j in df.columns:
                if j == c:
                    continue
                try:
                    if j in num:
                        v = num[j]
                        a, b = v[m & v.notna()], v[~m & v.notna()]
                        if len(a) >= 3 and len(b) >= 3 and (a.nunique() + b.nunique()) > 2:
                            p = stats.mannwhitneyu(a, b, alternative="two-sided").pvalue
                            resultados.append((p, j, "Mann-Whitney U"))
                    elif j in cat:
                        v = cat[j]
                        if 2 <= v.nunique() <= cfg.MAX_CATEGORIAS_TEST:
                            tabla = pd.crosstab(m, v.fillna("(ausente)"))
                            if tabla.shape[0] == 2 and tabla.shape[1] >= 2 and tabla.values.sum() >= 20:
                                p = stats.chi2_contingency(tabla)[1]
                                resultados.append((p, j, "Chi-cuadrada"))
                except ValueError:
                    continue

        if resultados:
            p_min, var, prueba = min(resultados, key=lambda r: r[0])
            p_adj = min(1.0, p_min * len(resultados))
            clasif = "MAR" if p_adj < alfa else "MCAR"
            filas.append(dict(columna=c, faltantes=k, pct_faltantes=round(100 * k / n, 2), clasificacion=clasif,
                              variable_asociada=var if clasif == "MAR" else "", prueba=prueba,
                              p_ajustado=round(p_adj, 5), pruebas_realizadas=len(resultados),
                              recalculables_desde_otras_columnas=recalculables))
        else:
            filas.append(dict(columna=c, faltantes=k, pct_faltantes=round(100 * k / n, 2),
                              clasificacion="NO EVALUABLE (muestra insuficiente)", variable_asociada="",
                              prueba="", p_ajustado=np.nan, pruebas_realizadas=0,
                              recalculables_desde_otras_columnas=recalculables))

    out = pd.DataFrame(filas)
    if out.empty:
        return pd.DataFrame(columns=["columna", "faltantes", "pct_faltantes", "clasificacion", "variable_asociada",
                                     "prueba", "p_ajustado", "pruebas_realizadas",
                                     "recalculables_desde_otras_columnas", "estrategia"])
    out["estrategia"] = out["columna"].map(_estrategia_imputacion)
    return out


def _estrategia_imputacion(col: str) -> str:
    if col in cfg.COLUMNAS_LLAVE:
        return "No imputar (llave): se conserva nula y se desvía a cuarentena en Fase 6"
    if col == "fecha":
        return "No imputar: la normalización de fechas es Fase 4 (Ing. 2)"
    if col == "moneda":
        return f"Asignar moneda por defecto de la fuente ({cfg.MONEDA_DEFAULT}) y marcar bandera"
    if col in cfg.COLUMNAS_NUMERICAS:
        return "1) Recalcular con cantidad*precio_unitario=monto_total; 2) mediana (por producto/moneda -> global)"
    if col in cfg.COLUMNAS_ANONIMO:
        return f"Etiqueta controlada '{cfg.ETIQUETA_ANONIMO}'"
    return f"Etiqueta controlada '{cfg.ETIQUETA_DESCONOCIDO}'"


# =============================================================================
# 4. FASES 2 y 3 — Limpieza, imputación y outliers
# =============================================================================


def _marcar(df: pd.DataFrame, mask, etiqueta: str) -> int:
    mask = pd.Series(mask, index=df.index).fillna(False).astype(bool)
    if mask.any():
        actual = df.loc[mask, "flags_ing1"].fillna("").astype("string")
        previo = actual.where(actual.eq("") | actual.str.endswith("|"), actual + "|")   # separador seguro
        df.loc[mask, "flags_ing1"] = previo + etiqueta + "|"
    return int(mask.sum())


def _normalizar_textos(df: pd.DataFrame, bit: dict) -> pd.DataFrame:
    """Quita espacios, unifica mayúsculas y convierte pseudo-nulos ('N/A', '-', ...) en NaN real."""
    total_pseudo = 0
    for c in df.columns:
        if c in ("fila_origen", "flags_ing1") or not _es_texto(df[c]):
            continue
        s = df[c]
        antes = int(s.isna().sum())
        t = s.astype("string").str.strip().str.replace(r"\s+", " ", regex=True)
        t = t.mask(t.str.lower().isin(list(cfg.TOKENS_NULOS)))
        total_pseudo += int(t.isna().sum()) - antes
        if c == "email":
            t = t.str.lower()
        elif c in cfg.COLUMNAS_NUMERICAS or c == "fecha":
            pass
        else:
            t = t.str.upper()
        df[c] = t
    if "moneda" in df.columns:
        df["moneda"] = df["moneda"].replace(cfg.MAPA_MONEDA)
    bit["pseudo_nulos_convertidos_a_nulo"] = total_pseudo
    return df


def _derivar(df: pd.DataFrame, bit: dict, etapa: str):
    """Recalcula campos faltantes cuando la información existe en otras columnas (Fase 2)."""
    if not all(c in df.columns for c in cfg.COLUMNAS_NUMERICAS):
        return
    q, p, m = df["cantidad"], df["precio_unitario"], df["monto_total"]
    mask = m.isna() & q.notna() & p.notna()
    df.loc[mask, "monto_total"] = (q * p)[mask]
    bit["monto_total_recalculado"] = bit.get("monto_total_recalculado", 0) + _marcar(df, mask, "monto_total:recalculado")

    q, p, m = df["cantidad"], df["precio_unitario"], df["monto_total"]
    mask = p.isna() & m.notna() & q.notna() & (q > 0)
    df.loc[mask, "precio_unitario"] = (m / q)[mask]
    bit["precio_unitario_recalculado"] = bit.get("precio_unitario_recalculado", 0) + _marcar(df, mask, "precio_unitario:recalculado")

    q, p, m = df["cantidad"], df["precio_unitario"], df["monto_total"]
    cand = (m / p.where(p > 0)).round()
    cercano = ((m / p.where(p > 0)) - cand).abs() < 0.1       # solo si el cociente es ~entero
    mask = q.isna() & m.notna() & p.notna() & (cand >= 1) & cercano
    df.loc[mask, "cantidad"] = cand[mask]
    bit["cantidad_recalculada"] = bit.get("cantidad_recalculada", 0) + _marcar(df, mask, "cantidad:recalculada")


def _imputar_mediana(df: pd.DataFrame, col: str, cascada: list, bit: dict, entero: bool = False):
    """Imputa con la MEDIANA: primero por grupo (p.ej. producto+moneda), luego global."""
    if col not in df.columns:
        return
    faltan = df[col].isna()
    if not faltan.any():
        return
    rel = df[col].copy()
    for grupo in cascada:
        grupo = [g for g in grupo if g in df.columns]
        if not grupo:
            continue
        med = df.groupby(grupo, dropna=True)[col].transform("median")
        rel = rel.fillna(med)
    rel = rel.fillna(df[col].median())
    if entero:
        rel = rel.round().clip(lower=1)
    df[col] = rel
    n = _marcar(df, faltan & df[col].notna(), f"{col}:mediana")
    bit[f"{col}_imputado_mediana"] = n
    if df[col].isna().any():
        bit["advertencias"].append(f"'{col}' sigue con nulos tras imputar (columna sin datos válidos).")


def _capear_iqr(df: pd.DataFrame, col: str, grupo: str | None, factor: float, entero: bool = False,
                aplicar: bool = True):
    """
    Winsorizing / capping con IQR (Fase 3): recorta a [Q1 - f*IQR, Q3 + f*IQR].
    Si `grupo` (p.ej. 'moneda') existe, los límites se calculan por grupo para no confundir USD con MXN.
    Con aplicar=False solo DETECTA (cuenta cuántos caerían fuera) sin modificar la columna.
    Devuelve (tabla_umbrales, mascara_capeados).
    """
    capeado = pd.Series(False, index=df.index)
    filas = []
    if col not in df.columns:
        return pd.DataFrame(), capeado
    claves = df[grupo].fillna("(sin_grupo)") if (grupo and grupo in df.columns) else pd.Series("TODAS", index=df.index)
    for g, sub in df.groupby(claves):
        vals = sub[col]
        if vals.notna().sum() < 4:
            filas.append(dict(columna=col, grupo=g, n=int(vals.notna().sum()), q1=np.nan, q3=np.nan, iqr=np.nan,
                              lim_inf=np.nan, lim_sup=np.nan, capeados_inf=0, capeados_sup=0,
                              accion="sin_datos_suficientes"))
            continue
        q1, q3 = vals.quantile(0.25), vals.quantile(0.75)
        iqr = q3 - q1
        lo, hi = q1 - factor * iqr, q3 + factor * iqr
        if entero:
            lo, hi = max(math.ceil(lo), 1), math.floor(hi)
            hi = max(hi, lo)
        bajo, alto = vals < lo, vals > hi
        if aplicar:
            df.loc[sub.index, col] = vals.clip(lower=lo, upper=hi)
            capeado.loc[sub.index] = (bajo | alto).fillna(False)
        filas.append(dict(columna=col, grupo=g, n=int(vals.notna().sum()), q1=q1, q3=q3, iqr=iqr, lim_inf=lo,
                          lim_sup=hi, capeados_inf=int(bajo.sum()), capeados_sup=int(alto.sum()),
                          accion="recortado" if aplicar else "solo_detectado"))
    return pd.DataFrame(filas), capeado


def limpiar_fuente(df_raw: pd.DataFrame, fuente: str, moneda_default: str = cfg.MONEDA_DEFAULT,
                   factor_iqr: float = cfg.FACTOR_IQR):
    """
    Ejecuta Fases 2 y 3 sobre UNA fuente ya homologada (ver homologar_columnas).

    Orden (importa):
      1. Normalizar textos y pseudo-nulos      5. Moneda por defecto
      2. Texto -> número en columnas numéricas 6. Derivar campos (monto = cantidad*precio, etc.)
      3. Eliminar duplicados exactos           7. Mediana (cantidad, precio) y categóricas controladas
      4. Valores imposibles                    8. Capping IQR (cantidad y monto_total; precio_unitario solo se diagnostica)

    Devuelve (df_limpio, descartados, bitacora, umbrales_iqr).
    """
    bit = {"fuente": fuente, "filas_entrada": int(len(df_raw)), "advertencias": []}
    df = df_raw.copy()
    fuera = [c for c in cfg.COLUMNAS_FUERA_DE_ESQUEMA if c in df.columns]
    if fuera:                      # p.ej. dirección de envío: dato personal sin uso analítico (sí se perfila en la Fase 1)
        df = df.drop(columns=fuera)
        bit["columnas_fuera_de_esquema_omitidas"] = fuera
    df.insert(0, "fila_origen", df.index)
    df["flags_ing1"] = ""
    descartados = []

    faltan_cols = [c for c in cfg.COLUMNAS_OBLIGATORIAS if c not in df.columns]
    if faltan_cols:
        bit["advertencias"].append(
            f"Columnas esperadas ausentes en la fuente: {faltan_cols}. Revisa cfg.ALIAS_COLUMNAS o pasa mapa_manual.")

    # 1-2. Textos y números
    df = _normalizar_textos(df, bit)
    invalidos_texto = 0
    for c in cfg.COLUMNAS_NUMERICAS:
        if c in df.columns:
            antes = int(df[c].isna().sum())
            df[c] = a_numero(df[c])
            invalidos_texto += int(df[c].isna().sum()) - antes
    bit["numericos_ilegibles_a_nulo"] = invalidos_texto

    # 3. Duplicados exactos (POS reenvía el mismo registro)
    cols_dup = [c for c in df.columns if c not in ("fila_origen", "flags_ing1")]
    dup = _hashable(df[cols_dup]).duplicated(keep="first") if len(df) else pd.Series(False, index=df.index)
    if dup.any():
        descartados.append(df[dup].assign(motivo_descarte="DUPLICADO_EXACTO"))
    df = df[~dup].copy()
    bit["duplicados_exactos_eliminados"] = int(dup.sum())

    # 4. Valores imposibles
    if "cantidad" in df.columns:
        imposible = df["cantidad"] <= 0
        if imposible.any():
            descartados.append(df[imposible].assign(motivo_descarte="CANTIDAD_NO_POSITIVA"))
        df = df[~imposible].copy()
        bit["cantidad_no_positiva_eliminadas"] = int(imposible.sum())
    for c in ("precio_unitario", "monto_total"):
        if c in df.columns:
            malo = df[c] <= 0
            df.loc[malo, c] = np.nan
            bit[f"{c}_no_positivo_a_nulo"] = _marcar(df, malo, f"{c}:invalido_a_nulo")

    # 5. Moneda
    if "moneda" not in df.columns:
        df["moneda"] = moneda_default
        bit["advertencias"].append(
            f"La fuente no trae columna de moneda: se asumió '{moneda_default}' para todos los registros.")
    else:
        sin_moneda = df["moneda"].isna()
        df.loc[sin_moneda, "moneda"] = moneda_default
        bit["moneda_asignada_por_defecto"] = _marcar(df, sin_moneda, f"moneda:default_{moneda_default}")
        extranas = sorted(set(df["moneda"].dropna()) - {"MXN", "USD"})
        if extranas:
            bit["advertencias"].append(f"Monedas no reconocidas (se conservan; Pydantic las rechazará): {extranas}")

    # 6-7. Imputación
    if all(c in df.columns for c in cfg.COLUMNAS_NUMERICAS):
        _derivar(df, bit, "inicial")
        _imputar_mediana(df, "cantidad", [], bit, entero=True)
        _derivar(df, bit, "post_cantidad")
        _imputar_mediana(df, "precio_unitario", [["id_producto", "moneda"], ["moneda"]], bit)
        _derivar(df, bit, "post_precio")
    else:
        for c in cfg.COLUMNAS_NUMERICAS:
            _imputar_mediana(df, c, [["moneda"]], bit, entero=(c == "cantidad"))

    for c in cfg.COLUMNAS_ANONIMO:
        if c not in df.columns:       # la fuente ni siquiera trae la columna (p.ej. la web no tiene id_cliente)
            df[c] = pd.Series(cfg.ETIQUETA_ANONIMO, index=df.index, dtype="string")
            bit[f"{c}_columna_ausente_en_fuente"] = _marcar(df, pd.Series(True, index=df.index), f"{c}:fuente_sin_columna")
            bit["advertencias"].append(f"La fuente no trae la columna '{c}': se etiquetó '{cfg.ETIQUETA_ANONIMO}' en todas las filas.")
            continue
        if c in df.columns:
            if not _es_texto(df[c]):          # p.ej. columna 100% nula leída como float
                df[c] = df[c].astype("string")
            mask = df[c].isna()
            df.loc[mask, c] = cfg.ETIQUETA_ANONIMO
            bit[f"{c}_anonimo"] = _marcar(df, mask, f"{c}:{cfg.ETIQUETA_ANONIMO}")
    excluidas = set(cfg.COLUMNAS_LLAVE + cfg.COLUMNAS_ANONIMO + cfg.COLUMNAS_NUMERICAS +
                    ["fecha", "moneda", "fila_origen", "flags_ing1"])
    for c in df.columns:
        if c not in excluidas and _es_texto(df[c]):
            mask = df[c].isna()
            if mask.any():
                df.loc[mask, c] = cfg.ETIQUETA_DESCONOCIDO
                bit[f"{c}_desconocido"] = _marcar(df, mask, f"{c}:{cfg.ETIQUETA_DESCONOCIDO}")
    for c in cfg.COLUMNAS_LLAVE + ["fecha"]:
        if c in df.columns:
            bit[f"{c}_nulo_sin_imputar"] = _marcar(df, df[c].isna(), f"{c}:nulo_sin_imputar")

    # 7b. Monto incoherente con cantidad x precio (p.ej. bots que inflan el total sin cambiar las unidades).
    #     La identidad contable manda: si cantidad y precio son datos observados (no imputados), se recalcula el monto.
    if all(c in df.columns for c in cfg.COLUMNAS_NUMERICAS):
        esperado = df["cantidad"] * df["precio_unitario"]
        imputado = df["flags_ing1"].str.contains(r"cantidad:mediana|precio_unitario:mediana", regex=True).fillna(False)
        incoherente = (df["monto_total"].notna() & esperado.notna() & ~imputado &
                       ((df["monto_total"] - esperado).abs() > np.maximum(0.01, 0.01 * esperado.abs())))
        df.loc[incoherente, "monto_total"] = esperado[incoherente]
        bit["monto_incoherente_recalculado"] = _marcar(df, incoherente, "monto_total:recalculado_por_incoherencia")

    # 8. Outliers: capping IQR
    tablas = []
    if "cantidad" in df.columns:
        t, cap_q = _capear_iqr(df, "cantidad", None, factor_iqr, entero=True)
        tablas.append(t)
        _marcar(df, cap_q, "cantidad:capeada")
    else:
        cap_q = pd.Series(False, index=df.index)
    # precio_unitario: atributo del catálogo -> solo se DETECTA (un producto caro no es un error). Se valida en Fase 4.
    if "precio_unitario" in df.columns:
        t, _ = _capear_iqr(df, "precio_unitario", "moneda", factor_iqr, aplicar=False)
        tablas.append(t)
    # Si se recortó la cantidad, el monto se recalcula para que siga siendo cantidad*precio.
    if all(c in df.columns for c in cfg.COLUMNAS_NUMERICAS):
        rehacer = cap_q & df["cantidad"].notna() & df["precio_unitario"].notna()
        df.loc[rehacer, "monto_total"] = (df["cantidad"] * df["precio_unitario"])[rehacer]
        _marcar(df, rehacer, "monto_total:recalculado_por_capping")
    if "monto_total" in df.columns:
        t, cap_m = _capear_iqr(df, "monto_total", "moneda", factor_iqr)
        tablas.append(t)
        _marcar(df, cap_m, "monto_total:capeado")

    # Tipos y redondeo finales
    if "cantidad" in df.columns:
        df["cantidad"] = df["cantidad"].round().astype("Int64")
    for c in ("precio_unitario", "monto_total"):
        if c in df.columns:
            df[c] = df[c].round(2)

    # Auditoría: monto que no coincide con cantidad*precio (dato original inconsistente o monto capeado)
    if all(c in df.columns for c in cfg.COLUMNAS_NUMERICAS):
        esperado = df["cantidad"].astype("float64") * df["precio_unitario"]
        dif = (df["monto_total"] - esperado).abs() > np.maximum(0.01, 0.01 * esperado.abs())
        bit["monto_difiere_de_cantidad_x_precio"] = _marcar(df, dif, "monto:difiere_qxp")

    validas = [t for t in tablas if not t.empty]
    umbrales = pd.concat(validas, ignore_index=True) if validas else pd.DataFrame(
        columns=["columna", "grupo", "n", "q1", "q3", "iqr", "lim_inf", "lim_sup", "capeados_inf", "capeados_sup", "accion"])
    if not umbrales.empty:
        for col in umbrales["columna"].unique():
            sub = umbrales[umbrales["columna"] == col]
            total = int(sub["capeados_inf"].sum() + sub["capeados_sup"].sum())
            solo_det = (sub["accion"] == "solo_detectado").all()
            bit[f"{col}_outliers_detectados_sin_recortar" if solo_det else f"{col}_capeados"] = total

    # Salida ordenada
    df["flags_ing1"] = df["flags_ing1"].str.rstrip("|")
    df.insert(1, "fuente", fuente)
    orden = ["fila_origen", "fuente"] + [c for c in cfg.COLUMNAS_CANONICAS if c in df.columns]
    resto = [c for c in df.columns if c not in orden + ["flags_ing1"]]
    df = df[orden + resto + ["flags_ing1"]].reset_index(drop=True)

    desc = pd.concat(descartados, ignore_index=True) if descartados else pd.DataFrame(
        columns=list(df_raw.columns) + ["fila_origen", "motivo_descarte"])
    bit["filas_descartadas"] = int(len(desc))
    bit["filas_salida"] = int(len(df))
    return df, desc, bit, umbrales


# =============================================================================
# 5. Reporte comparativo antes / después
# =============================================================================


def _estadisticos(s: pd.Series) -> dict:
    v = s.dropna()
    if len(v) == 0:
        return dict(n=0, media=np.nan, mediana=np.nan, desv_est=np.nan, minimo=np.nan, p95=np.nan,
                    maximo=np.nan, asimetria=np.nan)
    return dict(n=int(len(v)), media=v.mean(), mediana=v.median(), desv_est=v.std(), minimo=v.min(),
                p95=v.quantile(0.95), maximo=v.max(), asimetria=float(stats.skew(v)) if len(v) > 2 else np.nan)


def comparar_antes_despues(raw: pd.DataFrame, limpio: pd.DataFrame, resumen_antes: dict, perfil_antes: pd.DataFrame,
                           resumen_despues: dict, perfil_despues: pd.DataFrame, umbrales: pd.DataFrame,
                           bit: dict) -> dict:
    """Devuelve un dict de DataFrames: resumen, faltantes, numericas, outliers, intervenciones."""
    resumen = pd.DataFrame([
        ("Filas", resumen_antes["filas"], resumen_despues["filas"]),
        ("Duplicados exactos", resumen_antes["duplicados_exactos"], resumen_despues["duplicados_exactos"]),
        ("Filas con algún faltante", resumen_antes["filas_con_algun_faltante"], resumen_despues["filas_con_algun_faltante"]),
        ("Completitud global (%)", resumen_antes["completitud_global_pct"], resumen_despues["completitud_global_pct"]),
    ], columns=["metrica", "antes", "despues"])

    f = perfil_antes[["columna", "faltantes_total", "pct_faltantes"]].rename(
        columns={"faltantes_total": "faltantes_antes", "pct_faltantes": "pct_antes"})
    d = perfil_despues[["columna", "faltantes_total", "pct_faltantes"]].rename(
        columns={"faltantes_total": "faltantes_despues", "pct_faltantes": "pct_despues"})
    faltantes = f.merge(d, on="columna", how="left")

    filas = []
    for c in cfg.COLUMNAS_NUMERICAS:
        if c in raw.columns:
            filas.append({"columna": c, "momento": "antes", **_estadisticos(a_numero(raw[c]))})
        if c in limpio.columns:
            filas.append({"columna": c, "momento": "despues", **_estadisticos(pd.to_numeric(limpio[c], errors="coerce"))})
    numericas = pd.DataFrame(filas)

    inter = pd.DataFrame([(k, v) for k, v in bit.items() if k not in ("advertencias", "fuente") and isinstance(v, (int, float))],
                         columns=["intervencion", "registros"])
    return {"resumen": resumen, "faltantes": faltantes, "numericas": numericas,
            "outliers": umbrales, "intervenciones": inter}


def _md(df: pd.DataFrame, decimales: int = 2) -> str:
    if df is None or df.empty:
        return "_(sin datos)_\n"

    def fmt(v):
        if isinstance(v, (float, np.floating)):
            if pd.isna(v):
                return ""
            return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.{decimales}f}"
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            return ""
        return str(v)

    cab = "| " + " | ".join(map(str, df.columns)) + " |\n"
    sep = "|" + "|".join(["---"] * len(df.columns)) + "|\n"
    cuerpo = "".join("| " + " | ".join(fmt(v) for v in fila) + " |\n" for fila in df.itertuples(index=False))
    return cab + sep + cuerpo


def _grafica_boxplots(raw, limpio, ruta_png, titulo):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cols = [c for c in cfg.COLUMNAS_NUMERICAS if c in raw.columns and c in limpio.columns]
    if not cols:
        return None
    fig, axes = plt.subplots(1, len(cols), figsize=(4.6 * len(cols), 4.6))
    axes = np.atleast_1d(axes)
    for ax, c in zip(axes, cols):
        antes = a_numero(raw[c]).dropna()
        despues = pd.to_numeric(limpio[c], errors="coerce").dropna()
        antes, despues = antes[antes > 0], despues[despues > 0]
        if len(antes) and len(despues):
            ax.boxplot([antes, despues], tick_labels=["Antes", "Después"], showfliers=True, flierprops=dict(markersize=3))
            ax.set_yscale("log")
        ax.set_title(c)
        ax.grid(alpha=0.25, axis="y")
    fig.suptitle(f"{titulo} — distribución antes vs. después (escala log)")
    fig.tight_layout()
    fig.savefig(ruta_png, dpi=110)
    plt.close(fig)
    return ruta_png


def guardar_reporte(carpeta, fuente: str, raw, limpio, descartados, bit, umbrales, perfil_antes, perfil_despues,
                    resumen_antes, resumen_despues, faltantes_clasif, tablas) -> dict:
    """Escribe CSV/JSON/PNG/MD de la fuente en `carpeta` y devuelve las rutas."""
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    rutas = {}

    def w_csv(nombre, df):
        p = carpeta / f"{fuente}_{nombre}.csv"
        df.to_csv(p, index=False, encoding="utf-8")
        rutas[nombre] = p

    w_csv("limpio_ing1", limpio)
    w_csv("descartados_ing1", descartados)
    w_csv("perfil_antes", perfil_antes)
    w_csv("perfil_despues", perfil_despues)
    w_csv("faltantes_clasificacion", faltantes_clasif)
    w_csv("umbrales_iqr", umbrales)
    w_csv("estadisticos_numericos", tablas["numericas"])
    p = carpeta / f"{fuente}_bitacora.json"
    p.write_text(json.dumps(bit, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    rutas["bitacora"] = p
    png = _grafica_boxplots(raw, limpio, carpeta / f"reporte_calidad_{fuente}.png", f"Fuente: {fuente}")
    rutas["grafica"] = png

    # Texto de justificación mediana vs. media, generado con los números reales
    just = []
    for c in cfg.COLUMNAS_NUMERICAS:
        fila = tablas["numericas"][(tablas["numericas"]["columna"] == c) & (tablas["numericas"]["momento"] == "antes")]
        if len(fila):
            r = fila.iloc[0]
            if r["n"] and r["mediana"] and not pd.isna(r["media"]):
                razon = r["media"] / r["mediana"] if r["mediana"] else float("nan")
                just.append(f"- **{c}**: media = {r['media']:,.2f}, mediana = {r['mediana']:,.2f} "
                            f"(media/mediana = {razon:.2f}), asimetría = {r['asimetria']:.2f}. "
                            + ("Distribución sesgada a la derecha: la media está jalada por valores extremos, "
                               "por eso se imputa con la **mediana**." if r["asimetria"] > 1 else
                               "Asimetría moderada; se mantiene la mediana por ser robusta a extremos."))
    md = [f"# Reporte comparativo de calidad — fuente `{fuente}`\n",
          "## 1. Resumen antes vs. después\n", _md(tablas["resumen"]),
          "\n## 2. Faltantes por columna (incluye pseudo-nulos como 'N/A')\n", _md(tablas["faltantes"]),
          "\n## 3. Clasificación del mecanismo de ausencia (MCAR / MAR)\n", _md(faltantes_clasif, 4),
          "\n> Limitación: MNAR no se puede comprobar con los datos observados; se descarta por conocimiento del dominio.\n",
          "\n## 4. Estadísticos de columnas numéricas\n", _md(tablas["numericas"]),
          "\n### Justificación: mediana en lugar de media\n", "\n".join(just) + "\n",
          "\n## 5. Outliers: umbrales IQR (factor 1.5) y registros recortados\n", _md(umbrales),
          f"\n![Boxplots]({png.name if png else ''})\n",
          "\n## 6. Intervenciones registradas en la bitácora\n", _md(tablas["intervenciones"], 0)]
    if bit.get("advertencias"):
        md.append("\n## 7. Advertencias\n" + "\n".join(f"- {a}" for a in bit["advertencias"]) + "\n")
    p = carpeta / f"reporte_calidad_{fuente}.md"
    p.write_text("\n".join(md), encoding="utf-8")
    rutas["reporte_md"] = p
    return rutas
