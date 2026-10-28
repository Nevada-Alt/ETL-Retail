"""generar_diagrama.py — Dibuja el diagrama de flujo del pipeline (Mockup, Día 1) -> docs/diagrama_flujo.png"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Polygon

C1, C2, C3 = "#cfe3fa", "#d6f0e0", "#e6dcf7"      # Ing.1 azul · Ing.2 verde · Ing.3 morado
OK, BAD, SRC, EDGE = "#c8efc8", "#f9d0d0", "#fff1c9", "#3a3a3a"

fig, ax = plt.subplots(figsize=(11, 14.5))
ax.set_xlim(0, 11)
ax.set_ylim(0, 14.5)
ax.axis("off")


def caja(x, y, w, h, titulo, detalle="", color="white", fs=10.5):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle="round,pad=0.02,rounding_size=0.15",
                                fc=color, ec=EDGE, lw=1.3))
    if detalle:
        ax.text(x, y + h * 0.18, titulo, ha="center", va="center", fontsize=fs, fontweight="bold")
        ax.text(x, y - h * 0.22, detalle, ha="center", va="center", fontsize=fs - 2, color="#222")
    else:
        ax.text(x, y, titulo, ha="center", va="center", fontsize=fs, fontweight="bold")


def flecha(x1, y1, x2, y2, txt="", dx=0.1, color=EDGE):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle="-|>", color=color, lw=1.5))
    if txt:
        ax.text((x1 + x2) / 2 + dx, (y1 + y2) / 2, txt, fontsize=8.5, color=color, ha="left", va="center")


ax.text(5.5, 14.15, "Pipeline ETL robusto con zona de cuarentena — Retail & E-commerce omnicanal",
        ha="center", fontsize=13, fontweight="bold")

# Fuentes
caja(2.1, 13.0, 3.2, 0.8, "ventas_tienda_fisica.csv", "POS: duplicados, fechas DD/MM, nulos", SRC, 10)
caja(5.5, 13.0, 3.2, 0.8, "ventas_online.json", "web: USD/MXN, bots, correos malos", SRC, 10)
caja(8.9, 13.0, 3.2, 0.8, "catalogo_productos.csv", "catálogo maestro (llave)", SRC, 10)

caja(4.0, 11.55, 6.4, 1.0, "FASE 1 · Diagnóstico de calidad inicial", "completitud (%) · duplicados exactos · tipos inferidos", C1)
flecha(2.1, 12.6, 3.2, 12.05)
flecha(5.5, 12.6, 4.6, 12.05)
caja(4.0, 10.05, 6.4, 1.1, "FASE 2 · Faltantes e imputación", "MCAR/MAR · recalcular monto=cant×precio · mediana · ANONIMO", C1)
flecha(4.0, 11.05, 4.0, 10.6)
caja(4.0, 8.55, 6.4, 1.1, "FASE 3 · Outliers con IQR (factor 1.5)", "capping / winsorizing por moneda · solo se eliminan cantidades ≤ 0", C1)
flecha(4.0, 9.5, 4.0, 9.1)
caja(9.0, 9.35, 2.4, 1.3, "descartados_ing1.csv", "duplicados exactos y\ncantidad ≤ 0\n(con su motivo)", "#eeeeee", 9.5)
flecha(7.2, 8.85, 7.8, 9.05)
flecha(7.2, 9.95, 7.8, 9.65)

caja(4.0, 6.95, 6.4, 1.3, "FASE 4 · Integración y unificación",
     "fechas → YYYY-MM-DD · USD → MXN @ 19.50\ncruce con catálogo maestro · marca de huérfanos", C2)
flecha(4.0, 8.0, 4.0, 7.6)
# catálogo maestro -> Fase 4 (ruta por el costado derecho, sin cruzar otras cajas)
ax.plot([10.3, 10.3], [12.6, 6.95], color=EDGE, lw=1.4)
flecha(10.3, 6.95, 7.2, 6.95)
ax.text(8.75, 7.15, "catálogo maestro", fontsize=8.5, ha="center")

caja(4.0, 5.35, 6.4, 1.1, "FASE 5 · Transformación", "Min-Max [0,1] · discretización Bajo / Medio / Alto (terciles)", C2)
flecha(4.0, 6.3, 4.0, 5.9)

ax.add_patch(Polygon([[4.0, 4.55], [6.6, 3.85], [4.0, 3.15], [1.4, 3.85]], closed=True, fc=C3, ec=EDGE, lw=1.3))
ax.text(4.0, 3.95, "FASE 6 · Validación Pydantic", ha="center", fontsize=10.5, fontweight="bold")
ax.text(4.0, 3.65, "¿cumple el esquema por registro?", ha="center", fontsize=9)
flecha(4.0, 4.8, 4.0, 4.55)

caja(2.0, 1.95, 3.4, 1.0, "transacciones_limpias", "registros conformes", OK)
caja(6.4, 1.95, 3.9, 1.0, "cuarentena_errores", "motivo_rechazo_cuarentena", BAD)
flecha(2.8, 3.55, 2.0, 2.45, "SÍ", dx=-0.45, color="#1b7a1b")
flecha(5.2, 3.55, 6.2, 2.45, "NO (try/except ValidationError)", dx=0.1, color="#a01818")

caja(4.0, 0.55, 7.4, 0.8, "FASE 7 · Carga transaccional SQLite",
     "BEGIN · SAVEPOINT por registro · COMMIT   (si falla el lote: ROLLBACK)", C3, 10)
flecha(2.0, 1.45, 3.0, 0.97)
flecha(6.4, 1.45, 5.2, 0.97)

# Leyenda
for i, (c, t) in enumerate([(C1, "Ing. 1: Calidad, nulos y outliers"), (C2, "Ing. 2: Transformación e integración"),
                            (C3, "Ing. 3: Validación, resiliencia y BD")]):
    ax.add_patch(FancyBboxPatch((7.9, 5.75 - i * 0.55), 0.4, 0.3, boxstyle="round,pad=0.01", fc=c, ec=EDGE))
    ax.text(8.4, 5.9 - i * 0.55, t, fontsize=8.5, va="center")

Path("docs").mkdir(exist_ok=True)
fig.savefig("docs/diagrama_flujo.png", dpi=130, bbox_inches="tight", facecolor="white")
print("docs/diagrama_flujo.png")
