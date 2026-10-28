"""Pruebas del módulo del Ingeniero/a 3 (validacion.py + base_datos.py):   python3 test_validacion_bd.py"""
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd

import base_datos as bd
import validacion as val


def fila_valida(**kw):
    base = dict(fila_origen=1, fuente="fisica", id_venta="TF-1", fecha="2026-03-04", id_producto="P0001",
                nombre_producto="X", categoria="Y", cantidad=2, precio_unitario=100.0, monto_total=200.0,
                moneda="MXN", moneda_original="MXN", id_cliente="C1", email="a@b.com", metodo_pago="TARJETA",
                monto_total_minmax=0.5, segmento_monto="Medio", es_huerfano=False, flags_ing1="")
    base.update(kw)
    return base


def _validar(**kw):
    ok, bad, _ = val.validar_registros(pd.DataFrame([fila_valida(**kw)]))
    return ok, bad


def test_registro_valido_pasa():
    ok, bad = _validar()
    assert len(ok) == 1 and bad.empty and ok.loc[0, "fecha"] == "2026-03-04"


def test_cada_violacion_va_a_cuarentena_con_su_motivo():
    casos = [
        (dict(email="user@@x.com"), "email"), (dict(email="sin_arroba.com"), "email"),
        (dict(fecha="03/04/2026"), "fecha"), (dict(fecha="2026-02-31"), "fecha"), (dict(fecha=None), "fecha"),
        (dict(fecha="1899-01-01"), "fecha"), (dict(cantidad=0), "cantidad"), (dict(cantidad=2.5), "cantidad"),
        (dict(cantidad="abc"), "cantidad"), (dict(precio_unitario=-5.0, monto_total=-10.0), "precio_unitario"),
        (dict(precio_unitario=float("inf")), "precio_unitario"), (dict(moneda="USD"), "moneda"),
        (dict(moneda="EUR"), "moneda"), (dict(es_huerfano=True), "es_huerfano"), (dict(id_producto=None), "id_producto"),
        (dict(id_producto="p 1;DROP"), "id_producto"), (dict(fuente="telegram"), "fuente"),
        (dict(segmento_monto="Enorme"), "segmento_monto"), (dict(monto_total_minmax=1.7), "monto_total_minmax"),
        (dict(monto_total=999.0), "regla_de_negocio"), (dict(id_cliente=""), "id_cliente"),
    ]
    for kw, campo in casos:
        ok, bad = _validar(**kw)
        assert ok.empty and len(bad) == 1, f"debía rechazarse: {kw}"
        motivo = bad.loc[0, "motivo_rechazo_cuarentena"]
        assert campo in motivo, f"{kw}: motivo '{motivo}' no menciona '{campo}'"
        assert bad.loc[0, "etapa"] == "VALIDACION_PYDANTIC"


def test_anonimo_es_valido_y_discrepancia_documentada_se_tolera():
    assert len(_validar(email="ANONIMO")[0]) == 1
    assert len(_validar(monto_total=150.0, flags_ing1="monto_total:capeado|monto:difiere_qxp")[0]) == 1
    assert len(_validar(monto_total=150.0, flags_ing1="monto_total:capeado")[0]) == 0


def test_un_registro_malo_no_detiene_el_lote():
    df = pd.DataFrame([fila_valida(id_venta=f"V{i}", email="mal@@x" if i % 3 == 0 else "a@b.com") for i in range(30)])
    ok, bad, st = val.validar_registros(df)
    assert len(ok) == 20 and len(bad) == 10 and st["entrada"] == 30


def test_resumen_motivos():
    df = pd.DataFrame([fila_valida(email="x"), fila_valida(email="y"), fila_valida(es_huerfano=True)])
    _, bad, _ = val.validar_registros(df)
    r = val.resumen_motivos(bad)
    assert r.set_index("campo").loc["email", "registros"] == 2


def _bd_temporal():
    d = tempfile.mkdtemp()
    return bd.conectar(Path(d) / "t.db")


def test_carga_y_verificacion():
    conn = _bd_temporal()
    df = pd.DataFrame([fila_valida(id_venta=f"V{i}", email="mal" if i == 0 else "a@b.com") for i in range(10)])
    ok, bad, _ = val.validar_registros(df)
    st = bd.cargar(conn, ok, bad)
    v = bd.verificar(conn)
    assert st["insertados_limpias"] == 9 and v["filas_transacciones_limpias"] == 9 and v["filas_cuarentena_errores"] == 1
    assert v["integrity_check"] == "ok"
    q = conn.execute("SELECT motivo_rechazo_cuarentena FROM cuarentena_errores").fetchone()[0]
    assert "email" in q


def test_carga_idempotente_no_duplica():
    conn = _bd_temporal()
    ok, bad, _ = val.validar_registros(pd.DataFrame([fila_valida(id_venta=f"V{i}") for i in range(5)]))
    bd.cargar(conn, ok, bad)
    bd.cargar(conn, ok, bad)
    assert bd.verificar(conn)["filas_transacciones_limpias"] == 5


def test_rechazo_de_la_bd_solo_afecta_a_ese_registro_savepoint():
    conn = _bd_temporal()
    ok, bad, _ = val.validar_registros(pd.DataFrame([fila_valida(id_venta=f"V{i}") for i in range(5)]))
    ok.loc[2, "cantidad"] = -9                      # se salta Pydantic a propósito: lo frena el CHECK de la BD
    st = bd.cargar(conn, ok, bad)
    v = bd.verificar(conn)
    assert st["rechazados_por_bd"] == 1 and v["filas_transacciones_limpias"] == 4
    assert v["cuarentena_por_etapa"] == {"ERROR_BD": 1}


def test_rollback_total_deja_la_bd_como_estaba():
    conn = _bd_temporal()
    ok, bad, _ = val.validar_registros(pd.DataFrame([fila_valida(id_venta=f"V{i}") for i in range(5)]))
    bd.cargar(conn, ok, bad)
    antes = bd.verificar(conn)
    ok2 = pd.DataFrame([fila_valida(id_venta=f"N{i}") for i in range(3)])
    bad_roto = pd.DataFrame([{"fuente": "fisica", "id_venta": "X", "fila_origen": object(), "etapa": "E",
                              "motivo_rechazo_cuarentena": "m"}])            # tipo no soportado -> falla la transacción
    try:
        bd.cargar(conn, ok2, bad_roto)
        assert False, "debía lanzar CargaFallida"
    except bd.CargaFallida:
        pass
    assert bd.verificar(conn) == antes               # ROLLBACK: ni siquiera las 3 filas buenas quedaron
    assert not conn.in_transaction


def test_inyeccion_sql_se_guarda_como_texto():
    conn = _bd_temporal()
    malo = "x'); DROP TABLE transacciones_limpias;--"
    ok, bad, _ = val.validar_registros(pd.DataFrame([fila_valida(email=malo, id_venta="V1"), fila_valida(id_venta="V2")]))
    bd.cargar(conn, ok, bad)
    v = bd.verificar(conn)
    assert v["filas_transacciones_limpias"] == 1 and v["filas_cuarentena_errores"] == 1
    assert "DROP TABLE" in conn.execute("SELECT datos_originales_json FROM cuarentena_errores").fetchone()[0]


def test_bd_de_version_anterior_se_migra_sin_fallar():
    conn = bd.conectar(":memory:")
    conn.execute("CREATE TABLE transacciones_limpias (id_registro INTEGER PRIMARY KEY, viejo TEXT)")
    bd.crear_esquema(conn)                                  # esquema distinto -> se recrea
    cols = {r[1] for r in conn.execute("PRAGMA table_info(transacciones_limpias)")}
    assert {"sucursal", "estatus_pedido", "motivo"} & cols == {"sucursal", "estatus_pedido"} and "viejo" not in cols
    ok, bad, _ = val.validar_registros(pd.DataFrame([fila_valida(id_venta="V1", sucursal="COLIMA CENTRO", estatus_pedido="COMPLETADO")]))
    bd.cargar(conn, ok, bad)
    assert conn.execute("SELECT sucursal, estatus_pedido FROM transacciones_limpias").fetchone() == ("COLIMA CENTRO", "COMPLETADO")

if __name__ == "__main__":
    pruebas = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fallos = 0
    for f in pruebas:
        try:
            f()
            print(f"OK    {f.__name__}")
        except Exception as e:     # noqa
            fallos += 1
            print(f"FALLA {f.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(pruebas) - fallos}/{len(pruebas)} pruebas pasaron")
    raise SystemExit(1 if fallos else 0)
