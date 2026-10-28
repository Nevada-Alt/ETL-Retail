"""
generar_datos_demo.py — Datos SINTÉTICOS (semilla fija) que imitan los problemas descritos para el Grupo 1:
duplicados de POS, fechas mixtas, nulos en totales, USD vs MXN, correos malformados, bots con compras gigantes,
cantidades <= 0, textos tipo 'N/A', llaves huérfanas y SKUs sucios.

SOLO sirve para probar el pipeline. Cuando tengas los archivos oficiales, úsalos en su lugar.
Uso:  python3 generar_datos_demo.py [carpeta_salida]
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

rng = np.random.default_rng(2602)
CARPETA = Path(sys.argv[1] if len(sys.argv) > 1 else "datasets_demo")
CARPETA.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- catálogo
n_prod = 80
catalogo = pd.DataFrame({
    "id_producto": [f"P{i:04d}" for i in range(1, n_prod + 1)],
    "nombre": [f"Producto {i}" for i in range(1, n_prod + 1)],
    "categoria": rng.choice(["ROPA", "HOGAR", "ELECTRONICA", "DEPORTES", "JUGUETES"], n_prod),
    "precio_lista": np.clip(np.round(rng.lognormal(5.4, 0.8, n_prod), 2), 25, 4500),
})
catalogo.to_csv(CARPETA / "catalogo_productos.csv", index=False)
precio_de = dict(zip(catalogo.id_producto, catalogo.precio_lista))


def sku_sucio(s):
    r = rng.random()
    if r < 0.03:
        return s.lower()
    if r < 0.05:
        return f" {s} "
    if r < 0.065:
        return "P9999"          # huérfano (no está en el catálogo)
    return s


def fecha_mixta(f, p_dmy):
    return f.strftime("%d/%m/%Y") if rng.random() < p_dmy else f.strftime("%Y-%m-%d")


def correo(i, malo_p=0.03):
    r = rng.random()
    if r < malo_p:
        return rng.choice([f"user{i}@@ejemplo.com", f"user{i}ejemplo.com", f"user{i}@", f"@ejemplo.com"])
    return f"cliente{i}@ejemplo.com"


# ---------------------------------------------------------------- tienda física (CSV)
n = 3000
fechas = pd.to_datetime("2026-01-01") + pd.to_timedelta(rng.integers(0, 270, n), unit="D")
skus = rng.choice(catalogo.id_producto, n)
cant = np.clip(rng.poisson(1.4, n) + 1, 1, 12).astype(float)
precio = np.array([precio_de[s] for s in skus])
metodo = rng.choice(["EFECTIVO", "TARJETA", "tarjeta", "Tarjeta ", "TRANSFERENCIA"], n)
fis = pd.DataFrame({
    "ticket": [f"TF-{i:06d}" for i in range(1, n + 1)],
    "fecha_venta": [fecha_mixta(f, 0.7) for f in fechas],
    "sku": [sku_sucio(s) for s in skus],
    "cantidad": cant,
    "precio": precio,
    "total": np.round(cant * precio, 2),
    "id_cliente": [f"C{rng.integers(1, 900):04d}" for _ in range(n)],
    "correo": [correo(i) for i in range(n)],
    "metodo_pago": metodo,
    "sucursal": rng.choice(["COLIMA CENTRO", "VILLA DE ALVAREZ", "MANZANILLO"], n),
}).astype(object)

# Nulos: monto 4% (recalculable), precio 3%, cantidad 1.5%; correo nulo depende del método de pago (MAR)
fis.loc[rng.random(n) < 0.04, "total"] = np.nan
fis.loc[rng.random(n) < 0.03, "precio"] = np.nan
fis.loc[rng.random(n) < 0.015, "cantidad"] = np.nan
p_correo_nulo = np.where(fis["metodo_pago"].str.strip().str.upper() == "EFECTIVO", 0.45, 0.03)
fis.loc[rng.random(n) < p_correo_nulo, "correo"] = np.nan
fis.loc[rng.random(n) < 0.12, "id_cliente"] = np.nan
# Basura de formato
idx = rng.choice(n, 25, replace=False)
fis.loc[idx, "total"] = ["$" + f"{float(v):,.2f}" if pd.notna(v) else v for v in fis.loc[idx, "total"]]
fis.loc[rng.choice(n, 15, replace=False), "total"] = "N/A"
fis.loc[rng.choice(n, 10, replace=False), "precio"] = "s/d"
# Cantidades imposibles y compras gigantes
fis.loc[rng.choice(n, 20, replace=False), "cantidad"] = rng.choice([0, -1, -3], 20)
for i in rng.choice(n, 6, replace=False):
    fis.loc[i, "cantidad"] = float(rng.integers(150, 400))
    fis.loc[i, "total"] = np.nan if rng.random() < 0.5 else fis.loc[i, "total"]
# Duplicados de POS (misma fila reenviada)
dups = fis.sample(int(n * 0.035), random_state=1)
fis = pd.concat([fis, dups], ignore_index=True).sample(frac=1, random_state=7).reset_index(drop=True)
fis.to_csv(CARPETA / "ventas_tienda_fisica.csv", index=False)

# ---------------------------------------------------------------- online (JSON)
m = 1800
fechas = pd.to_datetime("2026-01-01") + pd.to_timedelta(rng.integers(0, 270 * 24 * 3600, m), unit="s")
skus = rng.choice(catalogo.id_producto, m)
moneda = rng.choice(["MXN", "USD", "mxn", "pesos", "usd"], m, p=[0.55, 0.2, 0.08, 0.07, 0.10])
es_usd = np.isin(np.char.upper(moneda.astype(str)), ["USD"])
cant = np.clip(rng.poisson(1.2, m) + 1, 1, 10).astype(float)
precio = np.array([precio_de[s] for s in skus]) / np.where(es_usd, 19.5, 1.0)
precio = np.round(precio, 2)
registros = []
for i in range(m):
    f = fechas[i]
    registros.append({
        "order_id": f"WEB-{i + 1:06d}",
        "order_date": f.strftime("%Y-%m-%dT%H:%M:%S") if rng.random() < 0.8 else f.strftime("%d/%m/%Y"),
        "sku": sku_sucio(skus[i]),
        "qty": float(cant[i]),
        "unit_price": float(precio[i]),
        "total": round(float(cant[i] * precio[i]), 2),
        "currency": str(moneda[i]),
        "customer_id": f"C{rng.integers(1, 900):04d}",
        "customer_email": correo(i + 5000),
        "payment_method": str(rng.choice(["card", "paypal", "oxxo", "CARD"])),
    })
for r in registros:
    if rng.random() < 0.05:
        r["total"] = None
    if rng.random() < 0.03:
        r["unit_price"] = None
    if rng.random() < 0.02:
        r["qty"] = None
    if rng.random() < 0.01:
        r["currency"] = None
    if rng.random() < 0.15 and r["payment_method"].lower() == "oxxo":
        r["customer_email"] = None
    if rng.random() < 0.01:
        r["total"] = "N/A"
    if rng.random() < 0.01:
        r["qty"] = -1
for i in rng.choice(m, 8, replace=False):   # bots
    registros[i]["qty"] = float(rng.integers(500, 2500))
    registros[i]["total"] = round(registros[i]["qty"] * (registros[i]["unit_price"] or 100), 2)
registros += [dict(r) for r in rng.choice(registros, 20)]        # reenvíos duplicados
with open(CARPETA / "ventas_online.json", "w", encoding="utf-8") as f:
    json.dump(registros, f, ensure_ascii=False, indent=1)

(CARPETA / "_DATOS_DEMO.txt").write_text(
    "Estos archivos son SINTETICOS (generar_datos_demo.py). Borra este marcador cuando uses los datasets oficiales.\n",
    encoding="utf-8")
print(f"Datos demo escritos en {CARPETA.resolve()}")
