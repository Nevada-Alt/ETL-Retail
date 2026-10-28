"""
validacion.py — Ingeniero/a 3: Validación con Pydantic y Zona de Cuarentena (Fase 6)

Cada registro se valida de forma INDEPENDIENTE contra el esquema `VentaLimpia`.
    · conforme  -> lista de registros aptos (se cargan a `transacciones_limpias`)
    · viola     -> `ValidationError` capturado en try/except, se desvía a cuarentena con la columna
                   `motivo_rechazo_cuarentena` (campo + causa + valor recibido)
Un registro corrupto jamás detiene el lote.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter
from typing import Literal, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

import config as cfg

_RE_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
_RE_ID = re.compile(r"^[A-Z0-9][A-Z0-9_.\-]*$")


class VentaLimpia(BaseModel):
    """Esquema estricto de una venta lista para `transacciones_limpias`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    fila_origen: int = Field(ge=0)
    fuente: Literal["fisica", "online"]
    id_venta: str = Field(min_length=1, max_length=64)
    fecha: dt.date
    id_producto: str = Field(min_length=1, max_length=32)
    nombre_producto: Optional[str] = Field(default=None, max_length=200)
    categoria: Optional[str] = Field(default=None, max_length=100)
    cantidad: int = Field(ge=1, le=cfg.CANTIDAD_MAXIMA)
    precio_unitario: float = Field(gt=0, allow_inf_nan=False)
    monto_total: float = Field(gt=0, allow_inf_nan=False)
    moneda: Literal["MXN"]
    moneda_original: Optional[str] = Field(default=None, max_length=10)
    id_cliente: str = Field(min_length=1, max_length=64)
    email: str = Field(max_length=254)
    metodo_pago: Optional[str] = Field(default=None, max_length=50)
    sucursal: Optional[str] = Field(default=None, max_length=80)
    estatus_pedido: Optional[str] = Field(default=None, max_length=30)
    monto_total_minmax: float = Field(ge=0, le=1, allow_inf_nan=False)
    segmento_monto: Literal["Bajo", "Medio", "Alto"]
    es_huerfano: bool = False
    flags_ing1: str = ""

    # ---- validadores de campo ------------------------------------------------
    @field_validator("id_venta", "id_producto")
    @classmethod
    def _id_formato(cls, v: str):
        if not _RE_ID.match(v):
            raise ValueError("formato de identificador inválido (solo A-Z, 0-9, '_', '-', '.')")
        return v

    @field_validator("fecha", mode="before")
    @classmethod
    def _fecha_iso_estricta(cls, v):
        if isinstance(v, dt.datetime):
            raise ValueError("la fecha debe venir como YYYY-MM-DD, sin hora")
        if isinstance(v, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()):
            raise ValueError("la fecha debe estar en formato ISO YYYY-MM-DD")
        return v

    @field_validator("fecha")
    @classmethod
    def _fecha_en_rango(cls, v: dt.date):
        if v < dt.date.fromisoformat(cfg.FECHA_MINIMA) or v > dt.date.today() + dt.timedelta(days=1):
            raise ValueError(f"fecha fuera de rango ({cfg.FECHA_MINIMA} a hoy)")
        return v

    @field_validator("email")
    @classmethod
    def _email_valido(cls, v: str):
        if v == cfg.ETIQUETA_ANONIMO:           # etiqueta controlada de la Fase 2
            return v
        if not _RE_EMAIL.match(v):
            raise ValueError("correo con formato inválido")
        return v

    @field_validator("es_huerfano")
    @classmethod
    def _sin_huerfanos(cls, v: bool):
        if v:
            raise ValueError("llave huérfana: el id_producto no existe en el catálogo maestro")
        return v

    # ---- regla de negocio entre campos ------------------------------------------
    @model_validator(mode="after")
    def _monto_coherente(self):
        # Se omite solo cuando el Ing. 1 ya documentó la discrepancia (monto recortado por IQR / dato origen inconsistente)
        if "monto:difiere_qxp" not in [f.strip() for f in self.flags_ing1.split("|")]:
            esperado = self.cantidad * self.precio_unitario
            if abs(self.monto_total - esperado) > max(0.01, cfg.TOLERANCIA_MONTO * abs(esperado)):
                raise ValueError("monto_total no coincide con cantidad x precio_unitario (tolerancia 1 %)")
        return self


CAMPOS = list(VentaLimpia.model_fields)

_TRADUCCION = {
    "missing": "campo obligatorio ausente",
    "string_type": "se esperaba texto y llegó otro tipo / nulo",
    "int_type": "se esperaba un entero y llegó otro tipo / nulo",
    "int_parsing": "no es un entero válido",
    "int_from_float": "se esperaba entero, llegó decimal",
    "float_type": "se esperaba un número y llegó otro tipo / nulo",
    "float_parsing": "no es un número válido",
    "finite_number": "número no finito (inf/NaN)",
    "bool_type": "se esperaba verdadero/falso",
    "bool_parsing": "no es un booleano válido",
    "date_type": "se esperaba una fecha y llegó otro tipo / nulo",
    "date_parsing": "fecha inválida",
    "date_from_datetime_parsing": "fecha inválida",
    "greater_than": "el valor debe ser mayor que el mínimo permitido",
    "greater_than_equal": "el valor es menor que el mínimo permitido",
    "less_than_equal": "el valor excede el máximo permitido",
    "string_too_short": "texto vacío o demasiado corto",
    "string_too_long": "texto demasiado largo",
    "literal_error": "valor no permitido",
}


def _py(v):
    """numpy / pandas -> tipos nativos; NaN / NA -> None."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, (np.generic,)):
        v = v.item()
    if isinstance(v, float) and np.isnan(v):
        return None
    return v


def _filas_a_dicts(df: pd.DataFrame) -> list[dict]:
    return [{k: _py(v) for k, v in fila.items()} for fila in df.astype(object).to_dict("records")]


def _corto(v, n=40):
    t = repr(v)
    return t if len(t) <= n else t[: n - 3] + "..."


def _motivo(err: ValidationError) -> str:
    partes = []
    for e in err.errors():
        campo = ".".join(str(x) for x in e["loc"]) or "regla_de_negocio"
        tipo = e["type"]
        if tipo == "value_error":
            texto = str(e["msg"]).replace("Value error, ", "")
        else:
            texto = _TRADUCCION.get(tipo, e["msg"])
        # En errores de campo se incluye el valor recibido; en reglas entre campos no (el input sería todo el registro)
        valor = "" if (tipo == "missing" or not e["loc"]) else f" [valor recibido: {_corto(e.get('input'))}]"
        partes.append(f"{campo}: {texto}{valor}")
    return "; ".join(partes)


def validar_registros(df: pd.DataFrame):
    """
    Valida fila por fila. Devuelve (df_validos, df_cuarentena, stats).
    `df_cuarentena` conserva las columnas originales y agrega `etapa` y `motivo_rechazo_cuarentena`.
    """
    validos, rechazados = [], []
    if df is None or df.empty:
        vacio = pd.DataFrame(columns=CAMPOS)
        return vacio, pd.DataFrame(columns=["etapa", "motivo_rechazo_cuarentena"]), {"entrada": 0, "validos": 0,
                                                                                    "cuarentena": 0}
    filas = _filas_a_dicts(df)
    for fila in filas:
        try:
            validos.append(VentaLimpia.model_validate(fila).model_dump(mode="json"))
        except ValidationError as e:
            rechazados.append({**fila, "etapa": "VALIDACION_PYDANTIC", "motivo_rechazo_cuarentena": _motivo(e)})
        except Exception as e:   # noqa: BLE001 — red de seguridad: ningún registro tumba el lote
            rechazados.append({**fila, "etapa": "ERROR_INESPERADO",
                               "motivo_rechazo_cuarentena": f"{type(e).__name__}: {str(e)[:200]}"})
    df_ok = pd.DataFrame(validos, columns=CAMPOS)
    df_bad = pd.DataFrame(rechazados) if rechazados else pd.DataFrame(columns=list(df.columns) + ["etapa", "motivo_rechazo_cuarentena"])
    stats = {"entrada": len(filas), "validos": len(df_ok), "cuarentena": len(df_bad),
             "pct_cuarentena": round(100 * len(df_bad) / len(filas), 2)}
    return df_ok, df_bad, stats


def resumen_motivos(df_cuarentena: pd.DataFrame) -> pd.DataFrame:
    """Cuenta cuántos registros fallaron por campo y causa (un registro puede fallar por varios campos)."""
    if df_cuarentena is None or df_cuarentena.empty:
        return pd.DataFrame(columns=["campo", "causa", "registros"])
    c = Counter()
    for m in df_cuarentena["motivo_rechazo_cuarentena"].dropna():
        for parte in str(m).split("; "):
            campo, _, resto = parte.partition(": ")
            causa = re.sub(r"\s*\[valor recibido:.*\]$", "", resto)
            c[(campo, causa)] += 1
    out = pd.DataFrame([(k[0], k[1], v) for k, v in c.items()], columns=["campo", "causa", "registros"])
    return out.sort_values("registros", ascending=False).reset_index(drop=True)


def fila_a_json(fila: dict) -> str:
    return json.dumps({k: _py(v) for k, v in fila.items()}, ensure_ascii=False, default=str)
