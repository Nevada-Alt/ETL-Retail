"""
test_estres.py — Ingeniero/a 3: Batería de pruebas de estrés con INYECCIÓN DE DATOS CORRUPTOS.

Cada escenario escribe archivos de entrada dañados y corre el pipeline COMPLETO. Criterios de aprobación:
  1. El pipeline NO lanza ninguna excepción (no crash); termina con un estado controlado.
  2. Si llegó a la BD: la reconciliación cuadra (entrada = descartadas + limpias + cuarentena): no se pierde ni inventa
     ningún registro, y `PRAGMA integrity_check` = ok.
  3. Aserciones propias del escenario (p. ej. la inyección SQL se guardó como texto y las tablas siguen existiendo).

    python3 test_estres.py            -> imprime tabla y escribe salida_estres/resultados_pruebas_estres.csv
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

import config as cfg
import pipeline

RAIZ = Path("salida_estres")
DS = Path("datasets")
N_BASE = 600
SQLI = "x'); DROP TABLE transacciones_limpias;--"
BASURA = ["", "N/A", "NULL", "###", "😀🔥", "1e999", "-1e999", "NaN", "inf", "-inf", SQLI, "<script>alert(1)</script>",
          "x" * 5000, "ÿñ€", "0", "-0", "9" * 40, " ", "\n", "None", "{}", "[]", "𝕏", "\\", "'", '"', ";", "--"]

_fis = pd.read_csv(DS / "ventas_tienda_fisica.csv", dtype=str, keep_default_na=False).head(N_BASE)
_onl = json.loads((DS / "ventas_online.json").read_text(encoding="utf-8"))[:N_BASE]
_cat = pd.read_csv(DS / "catalogo_productos.csv", dtype=str, keep_default_na=False)


# ----------------------------------------------------------------------------- utilidades de escritura
def w_csv(df, p, enc="utf-8"):
    df.to_csv(p, index=False, encoding=enc)
    return p


def w_json(obj, p):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


def ensuciar_df(df, p, seed=1):
    rng = np.random.default_rng(seed)
    out = df.copy().astype(object)
    mask = rng.random(out.shape) < p
    return out.mask(mask, rng.choice(np.array(BASURA, dtype=object), size=out.shape))


def ensuciar_json(registros, p, seed=2):
    rng = np.random.default_rng(seed)
    extra = [None, True, [1, 2], {"a": 1}, 10 ** 30, -5, 1e308, float("nan"), "", "N/A", SQLI, "😀"]
    out = []
    for r in registros:
        r = dict(r)
        for k in list(r):
            if rng.random() < p:
                r[k] = extra[rng.integers(len(extra))] if rng.random() < 0.6 else BASURA[rng.integers(len(BASURA))]
        out.append(r)
    return out


def base(d):
    return w_csv(_fis, d / "fis.csv"), w_json(_onl, d / "onl.json"), w_csv(_cat, d / "cat.csv")


# ----------------------------------------------------------------------------- escenarios
# cada preparador devuelve (fisica, online, catalogo) ; cada verificador recibe (resumen, carpeta_salida)
def e_base(d): return base(d)
def e_ruido_masivo(d): return w_csv(ensuciar_df(_fis, .35), d / "fis.csv"), w_json(ensuciar_json(_onl, .35), d / "onl.json"), w_csv(_cat, d / "cat.csv")
def e_ruido_extremo(d): return w_csv(ensuciar_df(_fis, .85, 7), d / "fis.csv"), w_json(ensuciar_json(_onl, .85, 8), d / "onl.json"), w_csv(_cat, d / "cat.csv")


def e_solo_encabezados(d):
    return w_csv(_fis.head(0), d / "fis.csv"), w_json(_onl, d / "onl.json"), w_csv(_cat, d / "cat.csv")


def e_fisica_0_bytes(d):
    (d / "fis.csv").write_bytes(b"")
    return d / "fis.csv", w_json(_onl, d / "onl.json"), w_csv(_cat, d / "cat.csv")


def e_json_truncado(d):
    txt = json.dumps(_onl, ensure_ascii=False)
    (d / "onl.json").write_text(txt[: len(txt) // 2], encoding="utf-8")
    return w_csv(_fis, d / "fis.csv"), d / "onl.json", w_csv(_cat, d / "cat.csv")


def e_online_inexistente(d):
    return w_csv(_fis, d / "fis.csv"), d / "no_existe.json", w_csv(_cat, d / "cat.csv")


def e_ambas_rotas(d):
    (d / "fis.csv").write_bytes(b"")
    (d / "onl.json").write_text("{{{ no es json", encoding="utf-8")
    return d / "fis.csv", d / "onl.json", w_csv(_cat, d / "cat.csv")


def e_catalogo_inexistente(d):
    return w_csv(_fis, d / "fis.csv"), w_json(_onl, d / "onl.json"), d / "no_hay_catalogo.csv"


def e_catalogo_sin_llave(d):
    return w_csv(_fis, d / "fis.csv"), w_json(_onl, d / "onl.json"), w_csv(pd.DataFrame({"foo": [1, 2], "bar": ["a", "b"]}), d / "cat.csv")


def e_todo_huerfano(d):
    cat = _cat.copy()
    cat["id_producto"] = "Z" + cat["id_producto"]
    return w_csv(_fis, d / "fis.csv"), w_json(_onl, d / "onl.json"), w_csv(cat, d / "cat.csv")


def e_fechas_imposibles(d):
    f = _fis.copy()
    f["fecha_venta"] = np.random.default_rng(3).choice(["31/02/2026", "99/99/9999", "2026-13-45", "ayer", "", "0000-00-00", "32/01/2026", "1900-01-01"], len(f))
    return w_csv(f, d / "fis.csv"), w_json(_onl, d / "onl.json"), w_csv(_cat, d / "cat.csv")


def e_inyeccion_sql_unicode(d):
    f = _fis.copy()
    f.loc[f.index[:50], "id_producto"] = SQLI
    f.loc[f.index[100:150], "id_ticket"] = "😀" * 10
    f.loc[f.index[150:200], "metodo_pago"] = "Robert'); DROP TABLE cuarentena_errores;--"
    f.loc[f.index[200:250], "sucursal"] = "'; DELETE FROM transacciones_limpias;--"
    o = [dict(r) for r in _onl]
    for r in o[:50]:
        r["customer_email"] = "'; DELETE FROM cuarentena_errores;--@x.com"
    return w_csv(f, d / "fis.csv"), w_json(o, d / "onl.json"), w_csv(_cat, d / "cat.csv")


def e_valores_extremos(d):
    f = _fis.copy()
    f.loc[f.index[:60], "cantidad"] = "1e12"
    f.loc[f.index[60:120], "precio_unitario"] = "1e308"
    f.loc[f.index[120:180], "monto_total_mxn"] = "-inf"
    f.loc[f.index[180:240], "cantidad"] = "9" * 40
    f.loc[f.index[240:300], "precio_unitario"] = "-1"
    o = [dict(r) for r in _onl]
    for i in range(0, 100):
        o[i]["units"] = 10 ** 30
        o[i]["unit_price"] = float("inf") if i % 2 else 1e308
    return w_csv(f, d / "fis.csv"), w_json(o, d / "onl.json"), w_csv(_cat, d / "cat.csv")


def e_monedas_raras(d):
    o = [dict(r) for r in _onl]
    for i, r in enumerate(o):
        r["currency"] = ["EUR", "BTC", None, "$", "usd ", "Dólares", "mxn", "???"][i % 8]
    return w_csv(_fis, d / "fis.csv"), w_json(o, d / "onl.json"), w_csv(_cat, d / "cat.csv")


def e_columnas_faltantes(d):
    return w_csv(_fis.drop(columns=["precio_unitario", "monto_total_mxn", "id_cliente"]), d / "fis.csv"), \
        w_json([{k: v for k, v in r.items() if k not in ("units", "currency", "customer_email")} for r in _onl], d / "onl.json"), w_csv(_cat, d / "cat.csv")


def e_columnas_duplicadas_y_extra(d):
    f = _fis.copy()
    f["total2"] = f["monto_total_mxn"]
    f["ID_Ticket "] = f["id_ticket"]
    f["col_basura"] = "x"
    txt = f.to_csv(index=False).replace("total2", "monto_total_mxn", 1)
    (d / "fis.csv").write_text(txt, encoding="utf-8")
    return d / "fis.csv", w_json(_onl, d / "onl.json"), w_csv(_cat, d / "cat.csv")


def e_encoding_latin1(d):
    f = _fis.copy()
    f["sucursal"] = "SUCURSAL MAÑANA ÁÉÍÓÚ"
    return w_csv(f, d / "fis.csv", "latin-1"), w_json(_onl, d / "onl.json"), w_csv(_cat, d / "cat.csv")


def e_catalogo_duplicados_y_nulos(d):
    cat = pd.concat([_cat, _cat.head(20), pd.DataFrame({"id_producto": [None, "", "N/A"], "nombre_producto": ["x"] * 3, "categoria": ["y"] * 3, "precio_base_mxn": ["1"] * 3})])
    return w_csv(_fis, d / "fis.csv"), w_json(_onl, d / "onl.json"), w_csv(cat, d / "cat.csv")


def e_lote_con_duplicados_x5(d):
    return w_csv(pd.concat([_fis] * 5, ignore_index=True), d / "fis.csv"), w_json(_onl * 5, d / "onl.json"), w_csv(_cat, d / "cat.csv")


def e_json_anidado_y_jsonl(d):
    anid = [{"order_id": f"W{i}", "order_date": "2026-03-01", "sku": "P0001", "qty": 1, "unit_price": 10.0, "total": 10.0,
             "currency": "MXN", "customer": {"id": "C1", "email": "a@b.com"}, "items": [{"sku": "P1"}, {"sku": "P2"}]} for i in range(50)]
    (d / "onl.json").write_text("\n".join(json.dumps(x) for x in anid), encoding="utf-8")        # JSON Lines
    return w_csv(_fis, d / "fis.csv"), d / "onl.json", w_csv(_cat, d / "cat.csv")


def e_epoch_extremos(d):
    o = [dict(r) for r in _onl]
    malos = [1789105020000, 9e18, -5, 0, "1789105020.0", "99999999999999999999", 10 ** 30, True, [1789105020], "hoy", 1789105020.5, 253402300800]
    for i, r in enumerate(o):
        if i % 2 == 0:
            r["order_timestamp"] = malos[(i // 2) % len(malos)]
    return w_csv(_fis, d / "fis.csv"), w_json(o, d / "onl.json"), w_csv(_cat, d / "cat.csv")


def e_bd_inaccesible(d):
    return base(d)


# ---- verificadores específicos
def v_base(r, s):
    assert r["estado"] == "OK" and r["reconciliacion"]["cuadra"]
    assert r["bd"]["filas_transacciones_limpias"] > 0


def v_fuente_rota(r, s):
    assert r["estado"] == "PARCIAL" and r["bd"]["filas_transacciones_limpias"] > 0     # la otra fuente SÍ se cargó


def v_abortado(r, s):
    assert r["estado"] == "ABORTADO" and r["errores"]


def v_todo_huerfano(r, s):
    assert r["bd"]["filas_transacciones_limpias"] == 0 and r["bd"]["filas_cuarentena_errores"] > 0
    assert "huérfana" in sqlite3.connect(s / cfg.NOMBRE_BD).execute("SELECT motivo_rechazo_cuarentena FROM cuarentena_errores LIMIT 1").fetchone()[0]


def v_fechas(r, s):
    c = sqlite3.connect(s / cfg.NOMBRE_BD)
    assert c.execute("SELECT COUNT(*) FROM transacciones_limpias WHERE fuente='fisica'").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM cuarentena_errores WHERE motivo_rechazo_cuarentena LIKE '%fecha%'").fetchone()[0] > 0


def v_sqli(r, s):
    c = sqlite3.connect(s / cfg.NOMBRE_BD)
    tablas = {t[0] for t in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"transacciones_limpias", "cuarentena_errores"} <= tablas                      # nadie borró las tablas
    assert c.execute("SELECT COUNT(*) FROM cuarentena_errores WHERE datos_originales_json LIKE '%DROP TABLE%'").fetchone()[0] > 0


def v_monedas(r, s):
    c = sqlite3.connect(s / cfg.NOMBRE_BD)
    assert c.execute("SELECT COUNT(*) FROM cuarentena_errores WHERE motivo_rechazo_cuarentena LIKE '%moneda%'").fetchone()[0] > 0
    assert c.execute("SELECT COUNT(*) FROM transacciones_limpias WHERE moneda <> 'MXN'").fetchone()[0] == 0


def v_dups(r, s):
    assert r["reconciliacion"]["descartadas_ing1"] >= 4 * (N_BASE - 1)


def v_bd_inaccesible(r, s):
    assert r["estado"] == "CARGA_REVERTIDA" and any("carga" in e for e in r["errores"])


ESCENARIOS = [
    ("01_base_sin_corrupcion", "Datos de referencia sin dañar", e_base, v_base),
    ("02_ruido_masivo_35pct", "35 % de las celdas reemplazadas por basura (emoji, inf, SQL, 5 000 caracteres, tipos raros)", e_ruido_masivo, None),
    ("03_ruido_extremo_85pct", "85 % de las celdas corruptas en ambas fuentes", e_ruido_extremo, None),
    ("04_solo_encabezados", "CSV físico con encabezados y 0 filas", e_solo_encabezados, None),
    ("05_csv_0_bytes", "Archivo CSV vacío (0 bytes)", e_fisica_0_bytes, v_fuente_rota),
    ("06_json_truncado", "JSON online cortado a la mitad", e_json_truncado, v_fuente_rota),
    ("07_online_inexistente", "El archivo online no existe", e_online_inexistente, v_fuente_rota),
    ("08_ambas_fuentes_rotas", "CSV vacío + JSON inválido", e_ambas_rotas, v_abortado),
    ("09_catalogo_inexistente", "No existe catalogo_productos.csv", e_catalogo_inexistente, v_abortado),
    ("10_catalogo_sin_llave", "Catálogo sin columna de llave reconocible", e_catalogo_sin_llave, v_abortado),
    ("11_todo_huerfano", "Ningún producto existe en el catálogo", e_todo_huerfano, v_todo_huerfano),
    ("12_fechas_imposibles", "Fechas 31/02, mes 13, '0000-00-00', 'ayer', vacías", e_fechas_imposibles, v_fechas),
    ("13_inyeccion_sql_unicode", "SQL injection y emojis en SKU, correo, ticket y método de pago", e_inyeccion_sql_unicode, v_sqli),
    ("14_valores_extremos", "1e12, 1e308, -inf, enteros de 40 dígitos, precios negativos", e_valores_extremos, None),
    ("15_monedas_raras", "EUR, BTC, '$', '???', nulas, 'Dólares'", e_monedas_raras, v_monedas),
    ("16_columnas_faltantes", "Faltan precio, total, correo, cantidad y moneda", e_columnas_faltantes, None),
    ("17_columnas_duplicadas", "Encabezados repetidos, con espacios y columnas basura", e_columnas_duplicadas_y_extra, None),
    ("18_encoding_latin1", "CSV guardado en Latin-1 con ñ y acentos", e_encoding_latin1, None),
    ("19_catalogo_duplicados_nulos", "Catálogo con llaves duplicadas, nulas y 'N/A'", e_catalogo_duplicados_y_nulos, None),
    ("20_lote_duplicado_x5", "Cada registro repetido 5 veces (reenvíos del POS)", e_lote_con_duplicados_x5, v_dups),
    ("21_json_anidado_jsonl", "JSON Lines con objetos anidados y listas de ítems", e_json_anidado_y_jsonl, None),
    ("22_bd_inaccesible", "La ruta de la BD es un directorio (no se puede abrir)", e_bd_inaccesible, v_bd_inaccesible),
    ("23_epoch_extremos", "Timestamps epoch en milisegundos, negativos, gigantes, booleanos y listas", e_epoch_extremos, None),
]


def generico(r, salida):
    assert isinstance(r, dict) and r["estado"] in {"OK", "PARCIAL", "ABORTADO", "CARGA_REVERTIDA"}, f"estado inesperado: {r.get('estado')}"
    assert (salida / "resumen_pipeline.json").exists(), "no se escribió el resumen"
    if r["estado"] in ("OK", "PARCIAL") and "bd" in r:
        assert r["reconciliacion"]["cuadra"], f"NO CUADRA: {r['reconciliacion']}"
        assert r["bd"]["integrity_check"] == "ok"


def main() -> int:
    if RAIZ.exists():
        shutil.rmtree(RAIZ)
    RAIZ.mkdir(parents=True)
    filas, fallos = [], 0
    for nombre, desc, prep, ver in ESCENARIOS:
        d = RAIZ / nombre
        d.mkdir()
        salida = d / "salida"
        t0 = time.time()
        res, detalle, ok = None, "", True
        try:
            fis, onl, cat = prep(d)
            if nombre.startswith("22_"):
                salida.mkdir()
                (salida / cfg.NOMBRE_BD).mkdir()                 # BD "inaccesible"
            res = pipeline.ejecutar_pipeline(fis, onl, cat, salida)
            generico(res, salida)
            if ver:
                ver(res, salida)
        except AssertionError as e:
            ok, detalle = False, f"AssertionError: {e}"
        except Exception as e:                                   # EXCEPCIÓN NO CONTROLADA = el pipeline colapsó
            ok, detalle = False, f"CRASH {type(e).__name__}: {e}"
            traceback.print_exc()
        rc = (res or {}).get("reconciliacion", {})
        filas.append({"escenario": nombre, "descripcion": desc, "resultado": "PASA" if ok else "FALLA",
                      "estado_pipeline": (res or {}).get("estado", "CRASH"),
                      "filas_entrada": rc.get("filas_entrada"), "descartadas_ing1": rc.get("descartadas_ing1"),
                      "en_limpias": rc.get("en_transacciones_limpias"), "en_cuarentena": rc.get("en_cuarentena_errores"),
                      "reconciliacion_cuadra": rc.get("cuadra"), "segundos": round(time.time() - t0, 1),
                      "errores_controlados": " | ".join((res or {}).get("errores", []))[:200], "detalle_falla": detalle})
        fallos += (not ok)
    df = pd.DataFrame(filas)
    df.to_csv(RAIZ / "resultados_pruebas_estres.csv", index=False, encoding="utf-8")
    pd.set_option("display.width", 250, "display.max_colwidth", 60)
    print("\n" + df[["escenario", "resultado", "estado_pipeline", "filas_entrada", "descartadas_ing1", "en_limpias",
                     "en_cuarentena", "reconciliacion_cuadra", "segundos"]].to_string(index=False))
    for f in filas:
        if f["detalle_falla"]:
            print(f"\n[FALLA] {f['escenario']}: {f['detalle_falla']}")
    print(f"\n{len(filas) - fallos}/{len(filas)} escenarios aprobados · sin excepciones no controladas: {fallos == 0}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
