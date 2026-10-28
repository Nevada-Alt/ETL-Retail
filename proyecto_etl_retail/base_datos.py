"""
base_datos.py — Ingeniero/a 3: Carga transaccional a SQLite (Fase 7)

Dos tablas obligatorias: `transacciones_limpias` y `cuarentena_errores`.

Garantías de la carga:
  · TODO el lote ocurre dentro de UNA transacción (BEGIN ... COMMIT). Si algo catastrófico falla -> ROLLBACK y la
    base queda exactamente como estaba antes de la corrida (no hay cargas a medias).
  · Cada INSERT va en su propio SAVEPOINT: si la BD rechaza un registro (CHECK, tipo, etc.) solo ESE registro hace
    rollback parcial y se desvía a cuarentena con etapa ERROR_BD; el lote continúa.
  · La carga es "reemplazo completo" e idempotente: correr el pipeline dos veces no duplica filas.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pandas as pd

import validacion as val

DDL = [
    """
    CREATE TABLE IF NOT EXISTS transacciones_limpias (
        id_registro          INTEGER PRIMARY KEY AUTOINCREMENT,
        fuente               TEXT    NOT NULL CHECK (fuente IN ('fisica','online')),
        id_venta             TEXT    NOT NULL,
        fecha                TEXT    NOT NULL CHECK (fecha GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
        id_producto          TEXT    NOT NULL,
        nombre_producto      TEXT,
        categoria            TEXT,
        cantidad             INTEGER NOT NULL CHECK (cantidad > 0),
        precio_unitario      REAL    NOT NULL CHECK (precio_unitario > 0),
        monto_total          REAL    NOT NULL CHECK (monto_total > 0),
        moneda               TEXT    NOT NULL CHECK (moneda = 'MXN'),
        moneda_original      TEXT,
        id_cliente           TEXT    NOT NULL,
        email                TEXT    NOT NULL,
        metodo_pago          TEXT,
        sucursal             TEXT,
        estatus_pedido       TEXT,
        monto_total_minmax   REAL    NOT NULL CHECK (monto_total_minmax BETWEEN 0 AND 1),
        segmento_monto       TEXT    NOT NULL CHECK (segmento_monto IN ('Bajo','Medio','Alto')),
        flags_ing1           TEXT    NOT NULL DEFAULT '',
        fila_origen          INTEGER NOT NULL,
        fecha_carga          TEXT    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_tl_fuente_venta ON transacciones_limpias (fuente, id_venta)",
    "CREATE INDEX IF NOT EXISTS ix_tl_producto ON transacciones_limpias (id_producto)",
    "CREATE INDEX IF NOT EXISTS ix_tl_fecha ON transacciones_limpias (fecha)",
    """
    CREATE TABLE IF NOT EXISTS cuarentena_errores (
        id_cuarentena               INTEGER PRIMARY KEY AUTOINCREMENT,
        fuente                      TEXT,
        id_venta                    TEXT,
        fila_origen                 INTEGER,
        etapa                       TEXT NOT NULL,
        motivo_rechazo_cuarentena   TEXT NOT NULL,
        datos_originales_json       TEXT,
        fecha_carga                 TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_cq_etapa ON cuarentena_errores (etapa)",
]

_COLS_LIMPIAS = ["fuente", "id_venta", "fecha", "id_producto", "nombre_producto", "categoria", "cantidad",
                 "precio_unitario", "monto_total", "moneda", "moneda_original", "id_cliente", "email", "metodo_pago",
                 "sucursal", "estatus_pedido", "monto_total_minmax", "segmento_monto", "flags_ing1", "fila_origen"]

_SQL_INS_LIMPIA = (f"INSERT INTO transacciones_limpias ({', '.join(_COLS_LIMPIAS)}, fecha_carga) "
                   f"VALUES ({', '.join('?' for _ in _COLS_LIMPIAS)}, ?)")
_SQL_INS_CUAR = ("INSERT INTO cuarentena_errores (fuente, id_venta, fila_origen, etapa, motivo_rechazo_cuarentena, "
                 "datos_originales_json, fecha_carga) VALUES (?,?,?,?,?,?,?)")


class CargaFallida(Exception):
    """La transacción completa se revirtió (ROLLBACK); la BD quedó como antes de la corrida."""


def conectar(ruta) -> sqlite3.Connection:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ruta, isolation_level=None)      # control manual de transacciones
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def crear_esquema(conn: sqlite3.Connection) -> None:
    # Si la BD viene de una versión anterior del esquema (columnas distintas), se recrean las tablas:
    # la carga es de reemplazo completo, así que no se pierde nada que el pipeline no vuelva a generar.
    esperadas = {"transacciones_limpias": set(_COLS_LIMPIAS) | {"id_registro", "fecha_carga"}}
    for tabla, cols in esperadas.items():
        actuales = {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})")}
        if actuales and actuales != cols:
            conn.execute(f"DROP TABLE {tabla}")
    for sentencia in DDL:
        conn.execute(sentencia)


def _txt(v):
    return None if v is None or (isinstance(v, float) and v != v) else str(v)


def _fila_cuarentena(r: dict, ahora: str):
    return (_txt(r.get("fuente")), _txt(r.get("id_venta")), val._py(r.get("fila_origen")),
            _txt(r.get("etapa")) or "VALIDACION_PYDANTIC", _txt(r.get("motivo_rechazo_cuarentena")) or "sin detalle",
            val.fila_a_json({k: v for k, v in r.items() if k not in ("etapa", "motivo_rechazo_cuarentena")}), ahora)


def cargar(conn: sqlite3.Connection, df_validos: pd.DataFrame, df_cuarentena: pd.DataFrame) -> dict:
    """Carga atómica. Devuelve estadísticas; lanza CargaFallida solo si la transacción completa se revierte."""
    ahora = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    crear_esquema(conn)
    insertados, fallidos_bd = 0, []
    filas_ok = val._filas_a_dicts(df_validos) if len(df_validos) else []
    filas_bad = val._filas_a_dicts(df_cuarentena) if len(df_cuarentena) else []

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM transacciones_limpias")
        conn.execute("DELETE FROM cuarentena_errores")
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('transacciones_limpias','cuarentena_errores')")

        for r in filas_ok:
            conn.execute("SAVEPOINT fila")
            try:
                conn.execute(_SQL_INS_LIMPIA, [r.get(c) for c in _COLS_LIMPIAS] + [ahora])
                conn.execute("RELEASE fila")
                insertados += 1
            except sqlite3.Error as e:                      # rollback parcial: solo este registro
                conn.execute("ROLLBACK TO fila")
                conn.execute("RELEASE fila")
                fallidos_bd.append({**r, "etapa": "ERROR_BD", "motivo_rechazo_cuarentena": f"{type(e).__name__}: {e}"})

        todos_cuarentena = filas_bad + fallidos_bd
        conn.executemany(_SQL_INS_CUAR, [_fila_cuarentena(r, ahora) for r in todos_cuarentena])
        conn.commit()                                       # COMMIT de toda la carga
    except Exception as e:                                  # noqa: BLE001
        try:
            conn.rollback()                                 # ROLLBACK completo: la BD queda como estaba
        except sqlite3.Error:
            pass
        raise CargaFallida(f"Transacción revertida: {type(e).__name__}: {e}") from e

    return {"insertados_limpias": insertados, "insertados_cuarentena": len(filas_bad) + len(fallidos_bd),
            "rechazados_por_bd": len(fallidos_bd), "fecha_carga": ahora}


def verificar(conn: sqlite3.Connection) -> dict:
    """Conteos y chequeos de integridad de lo que REALMENTE quedó en la base."""
    q = lambda sql: conn.execute(sql).fetchone()[0]                                   # noqa: E731
    return {
        "filas_transacciones_limpias": q("SELECT COUNT(*) FROM transacciones_limpias"),
        "filas_cuarentena_errores": q("SELECT COUNT(*) FROM cuarentena_errores"),
        "integrity_check": q("PRAGMA integrity_check"),
        "monto_total_mxn": round(q("SELECT COALESCE(SUM(monto_total),0) FROM transacciones_limpias"), 2),
        "por_fuente": dict(conn.execute("SELECT fuente, COUNT(*) FROM transacciones_limpias GROUP BY fuente").fetchall()),
        "por_segmento": dict(conn.execute("SELECT segmento_monto, COUNT(*) FROM transacciones_limpias GROUP BY segmento_monto").fetchall()),
        "cuarentena_por_etapa": dict(conn.execute("SELECT etapa, COUNT(*) FROM cuarentena_errores GROUP BY etapa").fetchall()),
    }


def exportar_tablas_csv(conn: sqlite3.Connection, carpeta) -> list[Path]:
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    rutas = []
    for t in ("transacciones_limpias", "cuarentena_errores"):
        p = carpeta / f"{t}.csv"
        pd.read_sql_query(f"SELECT * FROM {t}", conn).to_csv(p, index=False, encoding="utf-8")
        rutas.append(p)
    return rutas
