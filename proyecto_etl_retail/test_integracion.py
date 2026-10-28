"""Pruebas del módulo del Ingeniero/a 2 (integracion.py):   python3 test_integracion.py"""
import numpy as np
import pandas as pd

import integracion as ing


def test_fechas_formatos_mixtos_e_imposibles():
    df = pd.DataFrame({"fecha": ["2026-03-04", "04/03/2026", "2026-03-04T10:22:11", "31/02/2026", "ayer", None,
                                 "1/2/26", "2026/05/06", "13/13/2026", "04-03-2026"], "flags_ing1": [""] * 10})
    d, s = ing.normalizar_fechas(df)
    esperado = ["2026-03-04", "2026-03-04", "2026-03-04", None, None, None, "2026-02-01", "2026-05-06", None, "2026-03-04"]
    assert [None if pd.isna(x) else x for x in d["fecha"]] == esperado
    assert s["imposibles"] == 2 and s["formato_no_reconocido"] == 1 and s["nulas"] == 1
    assert "fecha_original" in d.columns and d.loc[1, "fecha_original"] == "04/03/2026"
    assert "fecha:convertida_a_ISO" in d.loc[1, "flags_ing1"] and "fecha:invalida_a_nulo" in d.loc[3, "flags_ing1"]


def test_dia_mes_anio_no_se_confunde_con_mes_dia():
    d, _ = ing.normalizar_fechas(pd.DataFrame({"fecha": ["03/04/2026"], "flags_ing1": [""]}))
    assert d.loc[0, "fecha"] == "2026-04-03"          # 3 de abril (DD/MM), no 4 de marzo


def test_usd_a_mxn_y_monedas_raras():
    df = pd.DataFrame({"moneda": ["USD", "MXN", "EUR"], "precio_unitario": [10.0, 100.0, 5.0],
                       "monto_total": [20.0, 100.0, 5.0], "flags_ing1": [""] * 3})
    d, s = ing.convertir_a_mxn(df, 19.50)
    assert d.loc[0, "precio_unitario"] == 195.0 and d.loc[0, "monto_total"] == 390.0 and d.loc[0, "moneda"] == "MXN"
    assert d.loc[0, "moneda_original"] == "USD" and d.loc[1, "monto_total"] == 100.0
    assert d.loc[2, "moneda"] == "EUR" and s["monedas_no_reconocidas"] == 1 and s["filas_usd_convertidas"] == 1


def test_catalogo_duplicados_y_huerfanos():
    cat, st = ing.preparar_catalogo(pd.DataFrame({"SKU": ["p1", "P1 ", "P2", None], "Nombre": ["a", "b", "c", "d"],
                                                  "Precio": ["10", "11", "12", "13"]}))
    assert list(cat.id_producto) == ["P1", "P2"] and st["ids_duplicados"] == 1 and st["ids_nulos"] == 1
    v = pd.DataFrame({"id_producto": ["P1", "P9", None], "flags_ing1": [""] * 3})
    d, s = ing.cruzar_catalogo(v, cat)
    assert list(d.es_huerfano) == [False, True, True] and s["huerfanos"] == 2 and s["huerfanos_llave_nula"] == 1
    assert len(d) == 3                                  # no se elimina nada
    assert d.loc[0, "nombre_producto"] == "A"


def test_minmax_rango_y_casos_degenerados():
    d, m = ing.escalar_minmax(pd.DataFrame({"monto_total": [10.0, 20.0, 30.0]}))
    assert list(d.monto_total_minmax) == [0.0, 0.5, 1.0] and m["minimo"] == 10 and m["maximo"] == 30
    d, _ = ing.escalar_minmax(pd.DataFrame({"monto_total": [5.0, 5.0]}))
    assert (d.monto_total_minmax == 0).all()            # sin variación: no divide entre cero
    d, _ = ing.escalar_minmax(pd.DataFrame({"monto_total": [np.nan, np.nan]}))
    assert d.monto_total_minmax.isna().all()


def test_discretizacion_terciles_y_reglas():
    df = pd.DataFrame({"monto_total": np.arange(1, 91, dtype=float)})
    d, s = ing.discretizar_monto(df)
    assert s["conteo"] == {"Bajo": 30, "Medio": 30, "Alto": 30}
    d, s = ing.discretizar_monto(df, cortes=(10, 50))
    assert s["conteo"] == {"Bajo": 10, "Medio": 40, "Alto": 40}
    d, s = ing.discretizar_monto(pd.DataFrame({"monto_total": [7.0, 7.0, 7.0]}))      # todos iguales: sin error
    assert d.segmento_monto.notna().all()


def test_integrar_no_crash_con_vacios():
    cat = pd.DataFrame({"id_producto": ["P1"]})
    for casos in ([], [pd.DataFrame()], [pd.DataFrame({"x": [1]})]):
        d, s = ing.integrar(casos, cat)
        assert isinstance(s, dict)


def test_fechas_epoch_segundos_milisegundos_y_basura():
    # 1789105020 s = 2026-09-11 05:37 UTC -> 2026-09-10 23:37 en Colima (UTC-6)
    vals = ["1789105020", "1789105020000", "1789105020.0", 1789105020, "9" * 30, "-5", "17891050"]
    d, s = ing.normalizar_fechas(pd.DataFrame({"fecha": vals, "flags_ing1": [""] * len(vals)}))
    assert list(d["fecha"].iloc[:4]) == ["2026-09-10"] * 4
    assert s["epoch"] == 4 and s["formato_no_reconocido"] == 3
    assert "fecha:convertida_a_ISO" in d.loc[0, "flags_ing1"]
    # cerca de medianoche UTC: en Colima todavía es el día anterior
    d, _ = ing.normalizar_fechas(pd.DataFrame({"fecha": ["1788998400"], "flags_ing1": [""]}))     # 2026-09-10 00:00 UTC
    assert d.loc[0, "fecha"] == "2026-09-09"

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
