"""
generar_reporte.py — Reporte PDF final (entregable del Día 5) construido con los resultados REALES de la última corrida.

    python3 test_estres.py          # (opcional pero recomendado) para incluir la batería de estrés
    python3 pipeline.py             # genera salida/
    python3 generar_reporte.py      # -> salida/Reporte_Proyecto_Integrador_ETL.pdf

Los nombres de integrantes se completan en la portada con --integrantes "Nombre1;Nombre2;Nombre3".
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

import config as cfg

ANCHO = letter[0] - 4 * cm
AZUL, GRIS, ROJO = colors.HexColor("#1f3a5f"), colors.HexColor("#f2f4f7"), colors.HexColor("#a01818")
_REEMPLAZOS = {"→": "->", "≤": "<=", "≥": ">=", "≈": "~", "·": "-", "≠": "!=", "✓": "OK", "•": "-"}

ss = getSampleStyleSheet()
ST = {
    "titulo": ParagraphStyle("t", parent=ss["Title"], fontSize=22, textColor=AZUL, spaceAfter=8, leading=26),
    "sub": ParagraphStyle("s", parent=ss["Normal"], fontSize=12, alignment=TA_CENTER, textColor=colors.HexColor("#444"), leading=16),
    "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontSize=15, textColor=AZUL, spaceBefore=10, spaceAfter=6),
    "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, textColor=AZUL, spaceBefore=8, spaceAfter=4),
    "p": ParagraphStyle("p", parent=ss["Normal"], fontSize=9.5, leading=13, spaceAfter=5),
    "peq": ParagraphStyle("peq", parent=ss["Normal"], fontSize=8, leading=10.5, textColor=colors.HexColor("#555")),
    "celda": ParagraphStyle("c", parent=ss["Normal"], fontSize=7.8, leading=9.6),
    "celdaH": ParagraphStyle("ch", parent=ss["Normal"], fontSize=7.8, leading=9.6, textColor=colors.white, fontName="Helvetica-Bold"),
    "alerta": ParagraphStyle("a", parent=ss["Normal"], fontSize=9.5, leading=13, textColor=ROJO, borderColor=ROJO,
                             borderWidth=1, borderPadding=6, spaceBefore=6, spaceAfter=10),
}


def t(s) -> str:
    s = str(s)
    for a, b in _REEMPLAZOS.items():
        s = s.replace(a, b)
    return escape(s)


def P(texto, estilo="p", markup=False):
    """Párrafo. markup=True permite <b></b> (el resto se escapa antes)."""
    if markup:
        s = str(texto)
        for a, b in _REEMPLAZOS.items():
            s = s.replace(a, b)
        return Paragraph(s, ST[estilo])
    return Paragraph(t(texto), ST[estilo])


def num(v, dec=2):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, bool):
        return "Sí" if v else "No"
    if isinstance(v, (int,)) or (isinstance(v, float) and float(v).is_integer() and abs(v) < 1e15):
        return f"{int(v):,}"
    if isinstance(v, float):
        return f"{v:,.{dec}f}"
    return str(v)


def tabla(df: pd.DataFrame, anchos=None, dec=2, cebra=True):
    if df is None or df.empty:
        return P("(sin datos)", "peq")
    filas = [[Paragraph(t(c), ST["celdaH"]) for c in df.columns]]
    for r in df.itertuples(index=False):
        filas.append([Paragraph(t(num(v, dec)), ST["celda"]) for v in r])
    if anchos:
        tot = sum(anchos)
        anchos = [ANCHO * a / tot for a in anchos]
    tb = Table(filas, colWidths=anchos, repeatRows=1)
    est = [("BACKGROUND", (0, 0), (-1, 0), AZUL), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c8ccd2")),
           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
           ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    if cebra:
        est += [("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, GRIS])]
    tb.setStyle(TableStyle(est))
    return tb


def imagen(ruta, ancho=ANCHO):
    from reportlab.lib.utils import ImageReader
    w, h = ImageReader(str(ruta)).getSize()
    return Image(str(ruta), width=ancho, height=ancho * h / w)


def pie(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#666"))
    canvas.drawString(2 * cm, 1.2 * cm, "TecNM Campus Colima - Ingeniería de Datos (ATD-2602) - Unidad 2 - Proyecto Integrador ETL - Grupo 1")
    canvas.drawRightString(letter[0] - 2 * cm, 1.2 * cm, f"Página {doc.page}")
    canvas.restoreState()


def _csv(ruta, **kw):
    ruta = Path(ruta)
    return pd.read_csv(ruta, **kw) if ruta.exists() else pd.DataFrame()


# =============================================================================


def construir(salida: Path, integrantes: list[str], estres: Path, destino: Path):
    R = json.loads((salida / "resumen_pipeline.json").read_text(encoding="utf-8"))
    hist = []
    nombres = (integrantes + ["______________________"] * 3)[:3]

    # ------------------------------------------------------------------ portada
    hist += [Spacer(1, 3 * cm), P("PROYECTO INTEGRADOR", "sub"), P("Pipeline ETL robusto con zona de cuarentena", "titulo"),
             P("Dominio: Retail & E-commerce omnicanal (venta física + tienda web)", "sub"), Spacer(1, 0.6 * cm),
             P("Ingeniería de Datos (ATD-2602) - Unidad 2: Limpieza y Preprocesamiento de Datos", "sub"),
             P("Instituto Tecnológico de Colima - Grupo 1 (LCS2) - Semestre Ago-Dic 2026", "sub"),
             P("Prof. Armando Román Gallardo Heredia", "sub"), Spacer(1, 1 * cm)]
    if R.get("datos_demo"):
        hist.append(P("ATENCIÓN: este reporte se generó con DATOS SINTÉTICOS de demostración, no con los datasets oficiales. "
                      "Las cifras sirven para validar el pipeline, no como resultados de la entrega. Para la versión final: "
                      "reemplazar los archivos de datasets/, borrar datasets/_DATOS_DEMO.txt y ejecutar de nuevo "
                      "pipeline.py y generar_reporte.py.", "alerta"))
    roles = pd.DataFrame({"Rol": ["Ingeniero/a 1", "Ingeniero/a 2", "Ingeniero/a 3"],
                          "Especialidad": ["Calidad, Nulos y Outliers", "Transformación e Integración", "Validación, Resiliencia y BD"],
                          "Integrante": nombres,
                          "Entregable clave": ["calidad.py + reporte antes/después", "integracion.py (multifuente y normalización)",
                                               "validacion.py + base_datos.py + pruebas de estrés"]})
    hist += [tabla(roles, [1.2, 2.2, 2.2, 3.2], cebra=False), Spacer(1, 0.6 * cm),
             P(f"Fecha de generación: {dt.date.today().isoformat()}", "peq"), PageBreak()]

    # ------------------------------------------------------------------ 1. objetivo + diagrama
    hist += [P("1. Objetivo y diseño del pipeline", "h1"),
             P("El pipeline ingiere datos crudos e inconsistentes de tres fuentes heterogéneas (CSV de tienda física, JSON de la "
               "tienda web y catálogo maestro), diagnostica su calidad, aplica preprocesamiento estadístico, valida cada registro "
               "con Pydantic y persiste los conformes en SQLite. Los registros inválidos se aíslan en una tabla de cuarentena "
               "con su motivo, sin detener el lote. El diagrama muestra el flujo completo de las 7 fases del pipeline y qué "
               "ingeniero es responsable de cada una."),
             imagen(Path("docs/diagrama_flujo.png"), ANCHO * 0.88), PageBreak()]

    # ------------------------------------------------------------------ 2. resumen ejecutivo
    rec = R.get("reconciliacion", {})
    bdv = R.get("bd", {})
    hist += [P("2. Resumen ejecutivo de la corrida", "h1")]
    filas = [("Estado del pipeline", R["estado"]), ("Duración (s)", R.get("duracion_segundos")),
             ("Filas de entrada (ambas fuentes)", rec.get("filas_entrada")),
             ("Descartadas en limpieza (duplicados exactos, cantidad <= 0)", rec.get("descartadas_ing1")),
             ("Registros aptos en transacciones_limpias", rec.get("en_transacciones_limpias")),
             ("Registros en cuarentena_errores", rec.get("en_cuarentena_errores")),
             ("Reconciliación (entrada = descartadas + limpias + cuarentena)", "CUADRA" if rec.get("cuadra") else "NO CUADRA"),
             ("Integridad de la base (PRAGMA integrity_check)", bdv.get("integrity_check")),
             ("Tasa USD -> MXN utilizada", R["parametros"]["tasa_usd_mxn"]), ("Factor IQR", R["parametros"]["factor_iqr"])]
    hist += [tabla(pd.DataFrame(filas, columns=["Indicador", "Valor"]), [4, 1.6]), Spacer(1, 0.3 * cm)]
    if R.get("errores"):
        hist.append(P("Errores controlados durante la corrida: " + " | ".join(R["errores"]), "alerta"))
    for a in R.get("advertencias", []):
        hist.append(P("Advertencia: " + a, "peq"))
    if rec:
        hist.append(P(f"Ningún registro se pierde en silencio: de {num(rec['filas_entrada'])} filas de entrada, "
                      f"{num(rec['descartadas_ing1'])} se descartaron con motivo documentado, {num(rec['en_transacciones_limpias'])} "
                      f"pasaron a la tabla limpia y {num(rec['en_cuarentena_errores'])} quedaron en cuarentena con su causa."))

    # ------------------------------------------------------------------ 3. Ing 1
    hist += [Spacer(1, 0.5 * cm), P("3. Fases 1 a 3 - Calidad, nulos y outliers (Ingeniero/a 1)", "h1")]
    for nombre, info in R["fuentes"].items():
        hist.append(P(f"3.{1 if nombre == 'fisica' else 2} Fuente: {nombre}", "h2"))
        if "error" in info:
            hist.append(P(f"No se pudo procesar esta fuente: {info['error']}", "alerta"))
            continue
        a, d, b = info["perfil_antes"], info["perfil_despues"], info["bitacora"]
        comp = pd.DataFrame([("Filas", a["filas"], d["filas"]), ("Duplicados exactos", a["duplicados_exactos"], d["duplicados_exactos"]),
                             ("Duplicados de id_venta", a["duplicados_id_venta"], d["duplicados_id_venta"]),
                             ("Filas con algún faltante", a["filas_con_algun_faltante"], d["filas_con_algun_faltante"]),
                             ("Completitud global (%)", a["completitud_global_pct"], d["completitud_global_pct"])],
                            columns=["Métrica", "Antes", "Después"])
        hist += [P("Fase 1 - Diagnóstico inicial y comparación antes/después", "p"), tabla(comp, [3, 1, 1]), Spacer(1, 0.2 * cm)]
        pa = _csv(salida / "ing1" / f"{nombre}_perfil_antes.csv")
        if len(pa):
            hist += [P("Perfil por columna (antes de transformar). 'pseudo_nulos' cuenta textos como N/A, - o s/d.", "peq"),
                     tabla(pa[["columna", "dtype", "tipo_inferido", "nulos", "pseudo_nulos", "pct_faltantes", "unicos", "nota"]],
                           [1.5, 0.9, 1.7, 0.7, 0.9, 0.9, 0.7, 2.6]), Spacer(1, 0.2 * cm)]
        falt = pd.DataFrame(info["faltantes_clasificacion"])
        if len(falt):
            hist += [P("Fase 2 - Clasificación del mecanismo de ausencia (MCAR / MAR)", "p"),
                     P("Para cada columna con faltantes se prueba si la ausencia se asocia con otras variables observadas "
                       "(Mann-Whitney U para numéricas, chi-cuadrada para categóricas, corrección de Bonferroni, alfa = 0.05). "
                       "MAR significa que la ausencia depende de otra variable; MCAR, que no hay evidencia de dependencia. "
                       "MNAR no es verificable con los datos observados y se descarta por el contexto del dominio.", "peq"),
                     tabla(falt[["columna", "faltantes", "pct_faltantes", "clasificacion", "variable_asociada", "p_ajustado",
                                 "recalculables_desde_otras_columnas"]], [1.5, 0.9, 0.9, 1.3, 1.5, 0.9, 1.5], dec=4),
                     Spacer(1, 0.2 * cm)]
        est = _csv(salida / "ing1" / f"{nombre}_estadisticos_numericos.csv")
        if len(est):
            hist += [P("Estadísticos de las variables numéricas, antes y después", "p"), tabla(est, [1.5, 0.8, 0.6, 0.9, 0.9, 0.9, 0.8, 0.9, 1, 0.8]),
                     Spacer(1, 0.15 * cm)]
            for c in cfg.COLUMNAS_NUMERICAS:
                f = est[(est["columna"] == c) & (est["momento"] == "antes")]
                if len(f) and pd.notna(f.iloc[0]["media"]) and f.iloc[0]["mediana"]:
                    r = f.iloc[0]
                    hist.append(P(f"{c}: media {r['media']:,.2f} vs. mediana {r['mediana']:,.2f} (razón {r['media'] / r['mediana']:.2f}); "
                                  f"asimetría {r['asimetria']:.2f}. " + ("La distribución está sesgada a la derecha: la media se infla con "
                                  "extremos (bots, compras gigantes), por eso se imputa con la mediana." if r["asimetria"] > 1 else
                                  "Se usa la mediana por ser robusta a valores extremos."), "peq"))
        um = _csv(salida / "ing1" / f"{nombre}_umbrales_iqr.csv")
        if len(um):
            hist += [P("Fase 3 - Umbrales IQR (factor 1.5) y registros afectados", "p"),
                     tabla(um[["columna", "grupo", "n", "q1", "q3", "lim_inf", "lim_sup", "capeados_inf", "capeados_sup", "accion"]],
                           [1.4, 0.8, 0.6, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 1.2]),
                     P("precio_unitario solo se diagnostica (es un atributo del catálogo); cantidad y monto_total se recortan al límite "
                       "(capping / winsorizing) sin eliminar la venta. Los límites de monto y precio se calculan por moneda.", "peq")]
        png = salida / "ing1" / f"reporte_calidad_{nombre}.png"
        if png.exists():
            hist += [Spacer(1, 0.15 * cm), imagen(png)]
        inter = [(k, v) for k, v in b.items() if isinstance(v, int) and k not in ("filas_entrada", "filas_salida") and v]
        if inter:
            hist += [P("Intervenciones registradas (cada una queda marcada en la columna flags_ing1)", "p"),
                     tabla(pd.DataFrame(inter, columns=["Intervención", "Registros"]), [3, 1])]
        for w in b.get("advertencias", []):
            hist.append(P("Advertencia: " + w, "peq"))
        hist.append(Spacer(1, 0.3 * cm))

    # ------------------------------------------------------------------ 4. Ing 2
    I = R.get("integracion", {})
    hist += [PageBreak(), P("4. Fases 4 y 5 - Integración y transformación (Ingeniero/a 2)", "h1")]
    f = I.get("fechas", {})
    hist += [P("Estandarización de fechas a YYYY-MM-DD", "h2"),
             P("Se aceptan fechas ISO (con o sin hora), AAAA/MM/DD, timestamps epoch (segundos o milisegundos, convertidos a hora de Colima UTC-6) y día/mes/año con / - o .; la convención para el formato con "
               "separadores es DÍA/MES/AÑO. Las fechas imposibles (31/02, mes 13) o ilegibles quedan nulas y marcadas, y la validación "
               "las envía a cuarentena."),
             tabla(pd.DataFrame([("Total de filas", f.get("filas")), ("Ya en formato ISO", f.get("iso")), ("Convertidas desde DD/MM/AAAA", f.get("dmy")), ("Convertidas desde epoch (timestamp)", f.get("epoch")),
                                 ("Convertidas desde AAAA/MM/DD", f.get("otro_formato_valido")),
                                 ("Ambiguas (día y mes <= 12)", f.get("dmy_ambiguas_dia_y_mes_le_12")), ("Fechas imposibles", f.get("imposibles")),
                                 ("Formato no reconocido", f.get("formato_no_reconocido")), ("Nulas", f.get("nulas"))], columns=["Concepto", "Registros"]), [3, 1])]
    m = I.get("moneda", {})
    c = I.get("catalogo", {})
    cat = R.get("catalogo", {})
    hist += [P("Conversión de moneda y cruce con el catálogo maestro", "h2"),
             tabla(pd.DataFrame([("Paridad oficial USD -> MXN", m.get("tasa_usd_mxn")), ("Filas convertidas de USD a MXN", m.get("filas_usd_convertidas")),
                                 ("Monedas no reconocidas (se rechazan en Fase 6)", m.get("monedas_no_reconocidas")),
                                 ("Productos en el catálogo (tras limpiar llaves)", cat.get("registros_salida")),
                                 ("Llaves duplicadas / nulas en el catálogo", f"{cat.get('ids_duplicados')} / {cat.get('ids_nulos')}"),
                                 ("Transacciones huérfanas (producto no existe en catálogo)", c.get("huerfanos")),
                                 ("Huérfanos por llave nula", c.get("huerfanos_llave_nula")),
                                 ("Productos distintos huérfanos", c.get("productos_distintos_huerfanos"))], columns=["Concepto", "Valor"]), [3, 1])]
    mm, sg = I.get("minmax", {}), I.get("segmentos", {})
    hist += [P("Escalamiento Min-Max y discretización del importe", "h2"),
             P(f"monto_total (MXN) se normaliza a [0, 1] con x' = (x - min) / (max - min); mínimo = {num(mm.get('minimo'))}, "
               f"máximo = {num(mm.get('maximo'))}. El importe se discretiza en tres segmentos por {sg.get('metodo', '')}: "
               f"Bajo hasta {num(sg.get('corte_bajo_medio'))} MXN, Medio hasta {num(sg.get('corte_medio_alto'))} MXN y Alto por encima. "
               "Los terciles producen segmentos equilibrados y no dependen de umbrales arbitrarios en una distribución asimétrica."),
             tabla(pd.DataFrame([(k, v) for k, v in (sg.get("conteo") or {}).items()], columns=["Segmento", "Registros"]), [2, 1])]

    # ------------------------------------------------------------------ 5. Ing 3
    V = R.get("validacion", {})
    hist += [PageBreak(), P("5. Fases 6 y 7 - Validación, cuarentena y base de datos (Ingeniero/a 3)", "h1"),
             P("Validación con Pydantic", "h2"),
             P("Cada registro se valida de forma independiente contra el esquema VentaLimpia (BaseModel con restricciones de campo, "
               "@field_validator y un @model_validator para la regla monto_total = cantidad x precio_unitario con tolerancia del 1 %). "
               "Un ValidationError se captura con try/except y el registro se desvía a cuarentena con la columna "
               "motivo_rechazo_cuarentena; el lote nunca se detiene."),
             tabla(pd.DataFrame([("Registros validados", V.get("entrada")), ("Aptos", V.get("validos")), ("A cuarentena", V.get("cuarentena")),
                                 ("% en cuarentena", V.get("pct_cuarentena"))], columns=["Concepto", "Valor"]), [3, 1]), Spacer(1, 0.2 * cm)]
    reglas = pd.DataFrame([
        ("fuente", "solo 'fisica' u 'online'"), ("id_venta / id_producto", "obligatorios; solo A-Z, 0-9, '_', '-', '.'"),
        ("fecha", "formato ISO estricto YYYY-MM-DD, entre 2000-01-01 y hoy"), ("cantidad", "entero entre 1 y 10,000"),
        ("precio_unitario / monto_total", "decimales finitos mayores que 0"), ("moneda", "solo MXN (tras la conversión)"),
        ("email", "formato válido o la etiqueta controlada ANONIMO"), ("es_huerfano", "debe ser falso (el producto existe en el catálogo)"),
        ("monto_total_minmax", "entre 0 y 1"), ("segmento_monto", "Bajo, Medio o Alto"),
        ("regla de negocio", "monto_total ~ cantidad x precio_unitario (1 %), salvo discrepancia ya documentada por el Ing. 1")],
        columns=["Campo", "Regla"])
    hist += [tabla(reglas, [1.6, 4]), Spacer(1, 0.2 * cm)]
    mot = _csv(salida / "resumen_motivos_cuarentena.csv")
    hist += [P("Causas de cuarentena (un registro puede fallar por más de un campo)", "h2"), tabla(mot.head(15), [1.3, 4, 0.9])]
    cq = _csv(salida / "cuarentena_errores.csv", dtype=str)
    if len(cq):
        cq["_causa"] = cq["etapa"].fillna("") + "|" + cq["motivo_rechazo_cuarentena"].fillna("").str.split(":").str[0]
        muestra = cq.groupby("_causa", group_keys=False).head(2).head(10)[["fuente", "id_venta", "etapa", "motivo_rechazo_cuarentena"]].copy()
        muestra["motivo_rechazo_cuarentena"] = muestra["motivo_rechazo_cuarentena"].str.slice(0, 150)
        hist += [P("Ejemplos de registros en cuarentena_errores (por tipo de causa)", "h2"), tabla(muestra, [0.8, 1.1, 1.9, 3.8])]
    hist += [P("Carga transaccional a SQLite", "h2"),
             P("La carga ocurre dentro de una sola transacción (BEGIN ... COMMIT). Cada INSERT usa su propio SAVEPOINT: si la base "
               "rechaza un registro (restricción CHECK, tipo, etc.) solo ese registro revierte y se desvía a cuarentena con etapa "
               "ERROR_BD. Si falla algo catastrófico se ejecuta ROLLBACK y la base queda como estaba. La carga reemplaza el contenido "
               "completo, por lo que correr el pipeline varias veces no duplica filas."),
             tabla(pd.DataFrame([("transacciones_limpias", "id_registro (PK), fuente, id_venta, fecha, id_producto, nombre_producto, categoria, cantidad, "
                                  "precio_unitario, monto_total, moneda, moneda_original, id_cliente, email, metodo_pago, sucursal, estatus_pedido, monto_total_minmax, "
                                  "segmento_monto, flags_ing1, fila_origen, fecha_carga. Restricciones CHECK en fecha, cantidad, precios, moneda, "
                                  "minmax y segmento."),
                                 ("cuarentena_errores", "id_cuarentena (PK), fuente, id_venta, fila_origen, etapa, motivo_rechazo_cuarentena (NOT NULL), "
                                  "datos_originales_json, fecha_carga")], columns=["Tabla", "Columnas"]), [1.4, 4.6])]
    if bdv:
        hist += [Spacer(1, 0.2 * cm), tabla(pd.DataFrame([("Filas en transacciones_limpias", bdv["filas_transacciones_limpias"]),
                                                          ("Filas en cuarentena_errores", bdv["filas_cuarentena_errores"]),
                                                          ("Monto total cargado (MXN)", bdv["monto_total_mxn"]),
                                                          ("Por fuente", json.dumps(bdv["por_fuente"], ensure_ascii=False)),
                                                          ("Por segmento", json.dumps(bdv["por_segmento"], ensure_ascii=False)),
                                                          ("Cuarentena por etapa", json.dumps(bdv["cuarentena_por_etapa"], ensure_ascii=False)),
                                                          ("integrity_check", bdv["integrity_check"])], columns=["Verificación sobre la BD", "Resultado"]), [2.2, 3.5])]

    # ------------------------------------------------------------------ 6. estrés
    hist += [PageBreak(), P("6. Resiliencia: pruebas de estrés con datos corruptos", "h1")]
    res_est = _csv(estres / "resultados_pruebas_estres.csv")
    if len(res_est):
        aprobados = int((res_est["resultado"] == "PASA").sum())
        hist += [P(f"Se inyectaron {len(res_est)} escenarios de corrupción (ruido masivo, archivos vacíos o truncados, fuentes inexistentes, "
                   f"fechas imposibles, SQL injection, valores extremos, monedas raras, columnas faltantes, duplicados masivos, BD inaccesible, "
                   f"entre otros) y se ejecutó el pipeline completo en cada uno. Resultado: {aprobados} de {len(res_est)} escenarios aprobados. "
                   "Criterios: el pipeline no lanza excepciones no controladas, la reconciliación de filas cuadra y la integridad de la base es correcta."),
                 tabla(res_est[["escenario", "descripcion", "resultado", "estado_pipeline", "en_limpias", "en_cuarentena", "reconciliacion_cuadra"]],
                       [2.3, 3.2, 0.7, 1.5, 0.8, 0.9, 0.9])]
    else:
        hist.append(P("Ejecutar python3 test_estres.py antes de generar este reporte para incluir la batería de estrés.", "alerta"))

    # ------------------------------------------------------------------ 7. conclusiones
    hist += [Spacer(1, 0.4 * cm), P("7. Conclusiones, supuestos y limitaciones", "h1")]
    concl = [
        "El pipeline completa las 7 fases de inicio a fin, aísla los registros inválidos con trazabilidad auditable y conserva la reconciliación exacta de filas.",
        "El capping con IQR 1.5 sobre cantidades enteras pequeñas puede recortar compras legítimas; es el método que pide la guía y el factor es configurable.",
        "Las fechas con separadores se interpretan como DÍA/MES/AÑO (convención mexicana); las ambiguas se cuantifican en la sección 4.",
        "Una moneda nula se asume MXN, y la tienda física se asume en MXN (su columna se llama monto_total_mxn); son supuestos, no hechos.",
        "Los pedidos online CANCELADO o DEVUELTO se conservan en transacciones_limpias con su estatus_pedido (no son datos inválidos); quien calcule ingresos debe filtrarlos.",
        "Cuando el total de un pedido no coincide con unidades x precio (totales inflados por bots), se recalcula con esa identidad; el hecho queda en la bandera monto_total:recalculado_por_incoherencia.",
        "La fuente online no trae id_cliente y la física no trae correo: esas columnas se etiquetan ANONIMO en toda la fuente (bandera fuente_sin_columna). La dirección de envío no se carga por ser dato personal sin uso analítico.",
        "MNAR no es verificable con los datos observados; se descarta por conocimiento del dominio.",
        "Min-Max y terciles se calculan sobre todos los registros integrados antes de la validación, siguiendo el orden de fases de la guía."]
    for x in concl:
        hist.append(P("- " + x))
    hist.append(P("Reglas de negocio completas y su justificación estadística: REGLAS_DE_NEGOCIO.md.", "peq"))

    doc = SimpleDocTemplate(str(destino), pagesize=letter, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=2 * cm,
                            title="Proyecto Integrador - Pipeline ETL con cuarentena", author="Grupo 1 - ATD-2602")
    doc.build(hist, onFirstPage=pie, onLaterPages=pie)
    return destino


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default="salida")
    ap.add_argument("--estres", default="salida_estres")
    ap.add_argument("--integrantes", default="", help='"Nombre 1;Nombre 2;Nombre 3" (Ing.1;Ing.2;Ing.3)')
    ap.add_argument("--destino", default=None)
    a = ap.parse_args()
    salida = Path(a.salida)
    destino = Path(a.destino) if a.destino else salida / "Reporte_Proyecto_Integrador_ETL.pdf"
    ints = [x.strip() for x in a.integrantes.split(";") if x.strip()]
    print(construir(salida, ints, Path(a.estres), destino))


if __name__ == "__main__":
    main()
