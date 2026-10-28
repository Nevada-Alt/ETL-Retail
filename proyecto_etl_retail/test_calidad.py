"""
Pruebas del módulo del Ingeniero/a 1 (sin dependencias extra):   python3 test_calidad.py
Incluye pruebas de "no crash" con datos corruptos (criterio de Resiliencia de la rúbrica).
"""
import numpy as np
import pandas as pd

import calidad as cal


def base(n=40):
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "id_venta": [f"V{i}" for i in range(n)],
        "fecha": ["2026-03-01"] * n,
        "id_producto": ["P1"] * n,
        "cantidad": rng.integers(1, 4, n).astype(float),
        "precio_unitario": [100.0] * n,
        "monto_total": np.nan,
        "moneda": ["MXN"] * n,
        "id_cliente": [f"C{i}" for i in range(n)],
        "email": [f"a{i}@x.com" for i in range(n)],
    }).assign(monto_total=lambda d: d.cantidad * d.precio_unitario)


def test_a_numero():
    s = pd.Series(["$1,234.50", "12,50", "abc", "N/A", " 7 ", None, "1.234,50", "100 MXN", "inf"])
    r = cal.a_numero(s)
    assert r.iloc[0] == 1234.5 and r.iloc[1] == 12.5 and np.isnan(r.iloc[2]) and np.isnan(r.iloc[3])
    assert r.iloc[4] == 7 and np.isnan(r.iloc[5]) and r.iloc[6] == 1234.5 and r.iloc[7] == 100 and np.isnan(r.iloc[8])


def test_perfil_cuenta_pseudonulos_y_duplicados():
    df = pd.DataFrame({"a": ["x", "N/A", "-", None, "x"], "b": [1, 2, 3, 4, 1]})
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    res, perfil = cal.perfilar_calidad(df, "t")
    fila = perfil[perfil.columna == "a"].iloc[0]
    assert fila.nulos == 1 and fila.pseudo_nulos == 2 and fila.faltantes_total == 3
    assert res["duplicados_exactos"] == 2          # fila 4 y la fila agregada repiten la fila 0


def test_derivaciones_e_imputacion():
    df = base()
    df.loc[0, "monto_total"] = np.nan                       # recalculable
    df.loc[1, "precio_unitario"] = np.nan                   # recalculable desde monto/cantidad
    df.loc[2, ["cantidad", "monto_total"]] = np.nan         # mediana de cantidad
    df.loc[3, "email"] = None
    limpio, desc, bit, _ = cal.limpiar_fuente(df, "t")
    assert limpio.loc[limpio.id_venta == "V0", "monto_total"].iloc[0] == df.loc[0, "cantidad"] * 100
    assert limpio.loc[limpio.id_venta == "V1", "precio_unitario"].iloc[0] == 100
    assert "cantidad:mediana" in limpio.loc[limpio.id_venta == "V2", "flags_ing1"].iloc[0]
    assert limpio.loc[limpio.id_venta == "V3", "email"].iloc[0] == "ANONIMO"
    assert limpio[["cantidad", "precio_unitario", "monto_total"]].isna().sum().sum() == 0


def test_solo_se_eliminan_cantidades_imposibles_y_duplicados():
    df = base()
    df.loc[5, "cantidad"] = 0
    df.loc[6, "cantidad"] = -2
    df = pd.concat([df, df.iloc[[10]]], ignore_index=True)  # duplicado exacto
    limpio, desc, bit, _ = cal.limpiar_fuente(df, "t")
    assert len(limpio) == len(df) - 3
    assert set(desc.motivo_descarte) == {"CANTIDAD_NO_POSITIVA", "DUPLICADO_EXACTO"}
    assert bit["filas_entrada"] == len(df) and bit["filas_salida"] == len(limpio)


def test_capping_no_elimina_filas_y_respeta_umbrales():
    df = base(200)
    df.loc[7, "cantidad"] = 5000                           # bot
    df.loc[7, "monto_total"] = 5000 * 100
    limpio, desc, bit, um = cal.limpiar_fuente(df, "t")
    assert len(limpio) == 200 and desc.empty
    fila = um[(um.columna == "cantidad")].iloc[0]
    assert limpio.cantidad.max() <= fila.lim_sup and "cantidad:capeada" in limpio.loc[limpio.id_venta == "V7", "flags_ing1"].iloc[0]
    # El monto del bot se recalculó con la cantidad recortada
    r = limpio.loc[limpio.id_venta == "V7"].iloc[0]
    assert r.monto_total <= r.cantidad * r.precio_unitario + 0.01


def test_capping_por_moneda_no_mezcla_usd_con_mxn():
    df = base(200)
    df.loc[df.index % 2 == 0, "moneda"] = "USD"
    df.loc[df.moneda == "USD", "precio_unitario"] = 5.0
    df["monto_total"] = df.cantidad * df.precio_unitario
    limpio, _, _, um = cal.limpiar_fuente(df, "t")
    assert set(um[um.columna == "monto_total"].grupo) == {"MXN", "USD"}
    assert (limpio.loc[limpio.moneda == "USD", "precio_unitario"] == 5.0).all()   # no se tocó el precio USD


def test_mar_vs_mcar():
    rng = np.random.default_rng(1)
    n = 800
    canal = rng.choice(["A", "B"], n)
    df = pd.DataFrame({"canal": canal, "x": rng.normal(size=n), "y": rng.normal(size=n)})
    df["mar"] = np.where(rng.random(n) < np.where(canal == "A", 0.5, 0.02), None, "v")
    df["mcar"] = np.where(rng.random(n) < 0.1, None, "v")
    r = cal.clasificar_faltantes(df).set_index("columna")
    assert r.loc["mar", "clasificacion"] == "MAR" and r.loc["mar", "variable_asociada"] == "canal"
    assert r.loc["mcar", "clasificacion"] == "MCAR"


def test_no_crash_datos_corruptos():
    casos = {
        "vacio": pd.DataFrame(),
        "solo_columnas": pd.DataFrame(columns=["id_venta", "cantidad", "precio_unitario", "monto_total"]),
        "todo_texto_basura": pd.DataFrame({"cantidad": ["x", "y", None], "precio_unitario": ["?", "-", "abc"],
                                           "monto_total": ["N/A", "", "1e999"], "id_venta": ["1", "1", "1"]}),
        "sin_columnas_esperadas": pd.DataFrame({"foo": [1, 2, 3], "bar": ["a", "b", None]}),
        "columna_toda_nula": base().assign(email=None, id_cliente=np.nan),
        "listas_en_celdas": base().assign(email=[["a", "b"]] * 40),
        "monedas_raras": base().assign(moneda=["EUR", "usd", "pesos", None] * 10),
        "valores_infinitos": base().assign(precio_unitario=np.inf),
    }
    for nombre, df in casos.items():
        try:
            cal.perfilar_calidad(df, nombre)
            cal.clasificar_faltantes(df)
            limpio, desc, bit, um = cal.limpiar_fuente(df, nombre)
        except Exception as e:     # noqa
            raise AssertionError(f"CRASH en caso '{nombre}': {type(e).__name__}: {e}")


def test_homologar_columnas():
    df = pd.DataFrame(columns=["Ticket", " Fecha Venta", "SKU", "Qty", "Precio", "Total", "Correo", "Currency"])
    nuevo, mapa = cal.homologar_columnas(df)
    assert list(nuevo.columns) == ["id_venta", "fecha", "id_producto", "cantidad", "precio_unitario",
                                   "monto_total", "email", "moneda"]


def test_columnas_reales_del_profesor_se_reconocen():
    fis = pd.DataFrame(columns=["id_ticket", "fecha_venta", "sucursal", "id_producto", "cantidad", "precio_unitario",
                                "monto_total_mxn", "id_cliente", "metodo_pago"])
    nuevo, _ = cal.homologar_columnas(fis)
    assert list(nuevo.columns) == ["id_venta", "fecha", "sucursal", "id_producto", "cantidad", "precio_unitario",
                                   "monto_total", "id_cliente", "metodo_pago"]
    web = pd.DataFrame(columns=["order_id", "order_timestamp", "product_id", "units", "currency", "unit_price",
                                "total_amount", "customer_email", "shipping_address", "order_status", "payment_gateway"])
    nuevo, _ = cal.homologar_columnas(web)
    assert list(nuevo.columns) == ["id_venta", "fecha", "id_producto", "cantidad", "moneda", "precio_unitario",
                                   "monto_total", "email", "direccion_envio", "estatus_pedido", "metodo_pago"]


def test_monto_inflado_se_recalcula_y_columna_ausente_es_anonimo():
    df = base(40).drop(columns=["id_cliente"])
    df.loc[3, "monto_total"] = df.loc[3, "cantidad"] * 100.0 * 80          # bot: total x80 con unidades normales
    df["direccion_envio"] = "Calle 1"
    limpio, desc, bit, _ = cal.limpiar_fuente(df, "online")
    fila = limpio[limpio.fila_origen == 3].iloc[0]
    assert abs(fila.monto_total - fila.cantidad * 100.0) < 0.01 and "recalculado_por_incoherencia" in fila.flags_ing1
    assert bit["monto_incoherente_recalculado"] == 1
    assert (limpio["id_cliente"] == "ANONIMO").all() and bit["id_cliente_columna_ausente_en_fuente"] == len(limpio)
    assert "direccion_envio" not in limpio.columns and bit["columnas_fuera_de_esquema_omitidas"] == ["direccion_envio"]

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
