"""
generar_visor.py — Visor web de los resultados del pipeline (un solo archivo HTML, funciona sin internet).

    python3 pipeline.py            # primero genera salida/
    python3 generar_visor.py       # -> salida/visor_datos.html   (abrir con doble clic)

Lee la base SQLite REAL (transacciones_limpias y cuarentena_errores) y los reportes de salida/, así que con los
datasets oficiales se actualiza solo. Todo el contenido de los datos se inserta como texto (no como HTML).
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

import config as cfg

COLS_LIMPIAS = ["fuente", "id_venta", "fecha", "id_producto", "nombre_producto", "categoria", "cantidad", "precio_unitario",
                "monto_total", "moneda_original", "id_cliente", "email", "metodo_pago", "sucursal", "estatus_pedido", "monto_total_minmax",
                "segmento_monto", "flags_ing1", "fila_origen"]


def _csv(p, **kw):
    p = Path(p)
    return pd.read_csv(p, **kw) if p.exists() else pd.DataFrame()


def _filas(df: pd.DataFrame):
    if df.empty:
        return []
    return df.astype(object).where(df.notna(), None).values.tolist()


def _registros(df: pd.DataFrame):
    if df.empty:
        return []
    return df.astype(object).where(df.notna(), None).to_dict("records")


def _b64(p: Path):
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode() if p.exists() else None


def _agr(serie_clave, df, valor="monto_total", top=None):
    g = df.assign(_k=serie_clave.fillna("(sin dato)")).groupby("_k").agg(n=("monto_total", "size"), monto=(valor, "sum"))
    g = g.sort_values("monto", ascending=False)
    if top:
        g = g.head(top)
    return [{"label": str(k), "n": int(r.n), "monto": round(float(r.monto), 2)} for k, r in g.iterrows()]


def preparar(salida: Path, docs: Path) -> dict:
    R = json.loads((salida / "resumen_pipeline.json").read_text(encoding="utf-8"))
    db = salida / cfg.NOMBRE_BD
    if not db.exists() or db.is_dir():
        raise SystemExit(f"No encuentro la base {db}. Ejecuta primero: python3 pipeline.py")
    conn = sqlite3.connect(db)
    L = pd.read_sql_query(f"SELECT {', '.join(COLS_LIMPIAS)} FROM transacciones_limpias ORDER BY id_registro", conn)
    Q = pd.read_sql_query("SELECT fuente, id_venta, fila_origen, etapa, motivo_rechazo_cuarentena, datos_originales_json "
                          "FROM cuarentena_errores ORDER BY id_cuarentena", conn)
    conn.close()

    agg = {}
    if len(L):
        mes = L["fecha"].str.slice(0, 7)
        porm = L.assign(_m=mes).groupby("_m").agg(n=("monto_total", "size"), monto=("monto_total", "sum")).sort_index()
        agg["por_mes"] = [{"label": k, "n": int(r.n), "monto": round(float(r.monto), 2)} for k, r in porm.iterrows()]
        agg["por_categoria"] = _agr(L["categoria"], L)
        agg["por_fuente"] = _agr(L["fuente"], L)
        agg["por_metodo_pago"] = _agr(L["metodo_pago"], L)
        agg["por_segmento"] = _agr(L["segmento_monto"], L)
        agg["por_moneda_original"] = _agr(L["moneda_original"], L)
        fis, onl = L[L["fuente"] == "fisica"], L[L["fuente"] == "online"]
        agg["por_sucursal"] = _agr(fis["sucursal"], fis) if len(fis) else []
        agg["por_estatus"] = _agr(onl["estatus_pedido"], onl) if len(onl) else []
        top = L.assign(_p=L["id_producto"] + " · " + L["nombre_producto"].fillna("")).groupby("_p").agg(
            n=("monto_total", "size"), monto=("monto_total", "sum")).sort_values("monto", ascending=False).head(10)
        agg["top_productos"] = [{"label": k, "n": int(r.n), "monto": round(float(r.monto), 2)} for k, r in top.iterrows()]
        cuentas, bordes = np.histogram(L["monto_total"].astype(float), bins=12)
        agg["hist_monto"] = [{"label": f"{bordes[i]:,.0f}", "value": int(cuentas[i]), "hasta": float(bordes[i + 1])} for i in range(len(cuentas))]
    if len(Q):
        agg["cuarentena_por_etapa"] = [{"label": k, "value": int(v)} for k, v in Q["etapa"].value_counts().items()]
        agg["cuarentena_por_fuente"] = [{"label": str(k), "value": int(v)} for k, v in Q["fuente"].fillna("(sin dato)").value_counts().items()]

    ing1 = {}
    for f in R.get("fuentes", {}):
        d = salida / "ing1"
        ing1[f] = {"perfil_antes": _registros(_csv(d / f"{f}_perfil_antes.csv")),
                   "perfil_despues": _registros(_csv(d / f"{f}_perfil_despues.csv")),
                   "umbrales": _registros(_csv(d / f"{f}_umbrales_iqr.csv")),
                   "estadisticos": _registros(_csv(d / f"{f}_estadisticos_numericos.csv")),
                   "grafica": _b64(d / f"reporte_calidad_{f}.png")}
    resumen = {k: R.get(k) for k in ("estado", "duracion_segundos", "datos_demo", "parametros", "reconciliacion", "integracion",
                                    "validacion", "catalogo", "bd", "errores", "advertencias")}
    resumen["fuentes"] = {f: {k: v for k, v in info.items() if k in ("perfil_antes", "perfil_despues", "bitacora", "faltantes_clasificacion", "error", "ruta")}
                          for f, info in R.get("fuentes", {}).items()}
    estres = _registros(_csv(Path("salida_estres") / "resultados_pruebas_estres.csv"))
    return {"generado": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "resumen": resumen, "agg": agg, "ing1": ing1,
            "motivos": _registros(_csv(salida / "resumen_motivos_cuarentena.csv")), "estres": estres,
            "diagrama": _b64(docs / "diagrama_flujo.png"),
            "limpias": {"cols": COLS_LIMPIAS, "rows": _filas(L)},
            "cuarentena": {"cols": ["fuente", "id_venta", "fila_origen", "etapa", "motivo", "original"], "rows": _filas(Q)}}


def _default(o):
    if isinstance(o, np.generic):
        return o.item()
    return str(o)


def construir(salida: Path, destino: Path, docs: Path = Path("docs")) -> Path:
    datos = preparar(salida, docs)
    js = json.dumps(datos, ensure_ascii=False, separators=(",", ":"), default=_default, allow_nan=False)
    js = js.replace("</", "<\\/").replace("<!--", "<\\!--")           # nada de los datos puede cerrar la etiqueta <script>
    html = PLANTILLA.replace("__DATOS_JSON__", js)
    destino.write_text(html, encoding="utf-8")
    return destino


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default="salida")
    ap.add_argument("--destino", default=None)
    a = ap.parse_args()
    salida = Path(a.salida)
    destino = Path(a.destino) if a.destino else salida / "visor_datos.html"
    p = construir(salida, destino)
    print(f"{p}  ({p.stat().st_size / 1e6:.1f} MB)  <- ábrelo con doble clic")


PLANTILLA = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Visor de datos · Pipeline ETL Retail</title>
<style>
:root{--bg:#f4f6fb;--card:#fff;--ink:#1b2433;--mut:#65718a;--line:#e3e8f1;--acc:#2f5fd0;--acc2:#5b8def;--ok:#17834a;--okbg:#e2f5ea;
--bad:#bf3a3a;--badbg:#fbe5e5;--warn:#a46a0d;--warnbg:#fdf1d9;--gry:#8791a5;--grybg:#eceff5;--sh:0 1px 2px rgba(20,30,60,.06),0 6px 20px rgba(20,30,60,.05)}
@media (prefers-color-scheme:dark){:root{--bg:#0f1420;--card:#171e2e;--ink:#e8edf7;--mut:#97a3bd;--line:#273049;--acc:#6f9bff;--acc2:#4a78e6;--ok:#58d08e;--okbg:#14352a;
--bad:#ff8585;--badbg:#3b1c1f;--warn:#f0bd6a;--warnbg:#3a2d12;--gry:#9aa5bd;--grybg:#222b40;--sh:0 1px 2px rgba(0,0,0,.4)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{background:linear-gradient(120deg,#1f3a8a,#2f5fd0 55%,#5b8def);color:#fff;padding:22px 24px 0}
header h1{margin:0;font-size:22px;letter-spacing:-.2px}header p{margin:4px 0 14px;opacity:.88;font-size:13px}
nav{display:flex;gap:4px;overflow-x:auto;padding:0 4px;margin:0 -4px;scrollbar-width:none}
nav button{background:transparent;border:0;color:#dbe6ff;padding:10px 16px;font:600 13.5px inherit;font-family:inherit;cursor:pointer;border-radius:10px 10px 0 0;white-space:nowrap}
nav button:hover{background:rgba(255,255,255,.12)}nav button.on{background:var(--bg);color:var(--acc)}
main{max-width:1280px;margin:0 auto;padding:20px 20px 60px}
section{display:none}section.on{display:block}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:var(--sh);padding:18px 20px;margin-bottom:16px}
.card h2{margin:0 0 4px;font-size:16px}.card h3{margin:14px 0 6px;font-size:13.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px}
.sub{color:var(--mut);font-size:12.5px;margin:0 0 12px}
.grid{display:grid;gap:14px}.g2{grid-template-columns:repeat(auto-fit,minmax(360px,1fr))}.g3{grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.kpis{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin-bottom:16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:var(--sh)}
.kpi .l{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.4px}.kpi .v{font-size:26px;font-weight:700;margin:2px 0;letter-spacing:-.5px}
.kpi .s{font-size:12px;color:var(--mut)}.kpi.ok .v{color:var(--ok)}.kpi.bad .v{color:var(--bad)}.kpi.acc .v{color:var(--acc)}
.chip{display:inline-block;padding:1px 9px;border-radius:99px;font-size:11.5px;font-weight:600;background:var(--grybg);color:var(--gry);white-space:nowrap}
.chip.ok{background:var(--okbg);color:var(--ok)}.chip.bad{background:var(--badbg);color:var(--bad)}.chip.warn{background:var(--warnbg);color:var(--warn)}.chip.acc{background:rgba(47,95,208,.12);color:var(--acc)}
.banner{border:1px solid var(--warn);background:var(--warnbg);color:var(--warn);border-radius:12px;padding:10px 14px;margin-bottom:16px;font-weight:600}
.flow{display:flex;height:34px;border-radius:10px;overflow:hidden;margin:10px 0 8px;background:var(--grybg)}
.flow div{display:flex;align-items:center;justify-content:center;color:#fff;font-size:12px;font-weight:700;min-width:2px;overflow:hidden}
.leg{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:var(--mut)}.leg i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px}
.hb{display:grid;grid-template-columns:minmax(90px,150px) 1fr minmax(70px,110px);gap:10px;align-items:center;margin:6px 0;font-size:12.5px}
.hb .t{height:12px;background:var(--grybg);border-radius:99px;overflow:hidden}.hb .t b{display:block;height:100%;background:linear-gradient(90deg,var(--acc2),var(--acc));border-radius:99px}
.hb .n{text-align:right;color:var(--mut);font-variant-numeric:tabular-nums}.hb .k{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cols{display:flex;align-items:flex-end;gap:6px;height:190px;padding-top:18px}.cols .c{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;min-width:0}
.cols .c b{width:78%;background:linear-gradient(180deg,var(--acc2),var(--acc));border-radius:6px 6px 2px 2px;min-height:2px}
.cols .c span{font-size:10.5px;color:var(--mut);margin-top:4px;white-space:nowrap}.cols .c em{font-style:normal;font-size:10.5px;color:var(--ink);margin-bottom:2px;white-space:nowrap}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px}
.bar input[type=search],.bar select{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:9px;padding:7px 10px;font:inherit;min-width:140px}
.bar input[type=search]{flex:1;min-width:200px}.bar button,.btn{background:var(--acc);color:#fff;border:0;border-radius:9px;padding:7px 13px;font:600 13px inherit;font-family:inherit;cursor:pointer}
.btn.sec{background:var(--grybg);color:var(--ink)}.btn:disabled{opacity:.4;cursor:default}
.tw{overflow:auto;border:1px solid var(--line);border-radius:12px;max-height:620px}
table{border-collapse:collapse;width:100%;font-size:12.8px;font-variant-numeric:tabular-nums}
th{position:sticky;top:0;background:var(--grybg);text-align:left;padding:8px 10px;font-size:11.5px;text-transform:uppercase;letter-spacing:.4px;color:var(--mut);cursor:pointer;white-space:nowrap;user-select:none;z-index:1}
th.num,td.num{text-align:right}td{padding:7px 10px;border-top:1px solid var(--line);vertical-align:top}tr.f:hover td{background:rgba(47,95,208,.06)}tr.f{cursor:default}
tr.x td{background:var(--grybg);white-space:pre-wrap;word-break:break-word;font:12px ui-monospace,Menlo,Consolas,monospace}
tr.fx{cursor:pointer}.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
.pg{display:flex;gap:8px;align-items:center;justify-content:space-between;margin-top:10px;color:var(--mut);font-size:12.5px;flex-wrap:wrap}
.pill{display:flex;gap:6px;margin-bottom:12px}.pill button{background:var(--grybg);color:var(--ink);border:0;border-radius:99px;padding:6px 14px;font:600 13px inherit;font-family:inherit;cursor:pointer}.pill button.on{background:var(--acc);color:#fff}
details summary{cursor:pointer;font-weight:600;color:var(--acc)}img{max-width:100%;height:auto;border-radius:10px}
.vs{display:grid;grid-template-columns:1fr auto 1fr;gap:10px;align-items:center;text-align:center}.vs .a{color:var(--mut)}.vs .d{font-weight:700;font-size:20px}.vs .arrow{color:var(--mut)}
.empty{padding:28px;text-align:center;color:var(--mut)}
@media (max-width:640px){main{padding:14px 10px 50px}.hb{grid-template-columns:90px 1fr 70px}header{padding:16px 14px 0}}
</style>
</head>
<body>
<header>
  <h1>Visor de datos · Pipeline ETL robusto</h1>
  <p id="sub"></p>
  <nav id="nav"></nav>
</header>
<main>
  <section id="t-resumen"></section>
  <section id="t-limpias"></section>
  <section id="t-cuarentena"></section>
  <section id="t-calidad"></section>
  <section id="t-integracion"></section>
  <section id="t-estres"></section>
</main>
<script id="datos" type="application/json">__DATOS_JSON__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("datos").textContent);
const R = D.resumen, AG = D.agg;
const $ = (s, el = document) => el.querySelector(s);
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) e.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return e;
}
const n = (v, d = 0) => v == null || v === "" || Number.isNaN(Number(v)) ? "—" : Number(v).toLocaleString("es-MX", {minimumFractionDigits: d, maximumFractionDigits: d});
const money = v => v == null ? "—" : "$" + n(v, 2);
const chip = (txt, tone) => h("span", {class: "chip" + (tone ? " " + tone : "")}, txt);
const MESES = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"];
const mesCorto = s => /^\d{4}-\d{2}$/.test(s) ? MESES[+s.slice(5) - 1] + " " + s.slice(2, 4) : s;

/* ---------- componentes ---------- */
function kpi(l, v, s, tone) { return h("div", {class: "kpi " + (tone || "")}, h("div", {class: "l"}, l), h("div", {class: "v"}, v), h("div", {class: "s"}, s || "")); }
function card(titulo, sub, ...contenido) { return h("div", {class: "card"}, titulo ? h("h2", {}, titulo) : null, sub ? h("p", {class: "sub"}, sub) : null, ...contenido); }
function barrasH(items, fmtv, maxv) {
  const m = maxv || Math.max(1, ...items.map(i => i.value ?? i.monto ?? 0));
  const host = h("div");
  for (const i of items) {
    const v = i.value ?? i.monto ?? 0;
    host.append(h("div", {class: "hb"}, h("div", {class: "k", title: i.label}, i.label),
      h("div", {class: "t"}, h("b", {style: "width:" + Math.max(1.5, 100 * v / m) + "%"})),
      h("div", {class: "n"}, fmtv ? fmtv(i) : n(v))));
  }
  return items.length ? host : h("div", {class: "empty"}, "Sin datos");
}
function columnas(items, fmtv, etiqueta) {
  const m = Math.max(1, ...items.map(i => i.value ?? i.monto ?? 0));
  const host = h("div", {class: "cols"});
  for (const i of items) {
    const v = i.value ?? i.monto ?? 0;
    host.append(h("div", {class: "c", title: i.label + ": " + (fmtv ? fmtv(i) : n(v))},
      h("em", {}, fmtv ? fmtv(i) : n(v)), h("b", {style: "height:" + Math.max(1, 100 * v / m * 0.82) + "%"}), h("span", {}, etiqueta ? etiqueta(i.label) : i.label)));
  }
  return items.length ? host : h("div", {class: "empty"}, "Sin datos");
}
const corto = v => v >= 1e6 ? n(v / 1e6, 1) + " M" : v >= 1e3 ? n(v / 1e3, 0) + " k" : n(v, 0);
function tablaSimple(cols, rows) {
  const t = h("table"), thead = h("thead"), tr = h("tr");
  cols.forEach(c => tr.append(h("th", {class: c.num ? "num" : ""}, c.t)));
  thead.append(tr); t.append(thead);
  const tb = h("tbody");
  rows.forEach(r => { const row = h("tr", {class: "f"}); cols.forEach(c => { const v = r[c.k]; row.append(h("td", {class: c.num ? "num" : ""}, c.f ? c.f(v, r) : (v ?? "—"))); }); tb.append(row); });
  t.append(tb);
  return h("div", {class: "tw", style: "max-height:420px"}, t);
}
function chipsFlags(s) {
  if (!s) return h("span", {style: "color:var(--mut)"}, "—");
  const f = String(s).split("|").filter(Boolean), w = h("span");
  f.slice(0, 2).forEach(x => w.append(chip(x), " "));
  if (f.length > 2) w.append(h("span", {class: "chip", title: f.slice(2).join("\n")}, "+" + (f.length - 2)));
  return w;
}
function descargarCSV(nombre, cols, filas) {
  const esc = v => { v = v == null ? "" : String(v); return /[",\n;]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
  const txt = "﻿" + cols.join(",") + "\n" + filas.map(r => r.map(esc).join(",")).join("\n");
  const a = h("a", {href: URL.createObjectURL(new Blob([txt], {type: "text/csv;charset=utf-8"})), download: nombre});
  document.body.append(a); a.click(); a.remove();
}

/* ---------- tabla interactiva: buscar, filtrar, ordenar, paginar, exportar ---------- */
function tablaInteractiva(host, o) {
  const rows = o.rows, hay = new Array(rows.length);
  let q = "", sort = null, page = 0, size = 25, vista = rows.map((_, i) => i), abierta = null;
  const filtros = o.filtros.map(f => ({...f, valor: ""}));
  const barra = h("div", {class: "bar"});
  const inp = h("input", {type: "search", placeholder: o.placeholder || "Buscar…", "aria-label": "Buscar"});
  barra.append(inp);
  filtros.forEach(f => {
    const vals = [...new Set(rows.map(r => f.get(r)).filter(v => v != null && v !== ""))].sort((a, b) => String(a).localeCompare(String(b), "es"));
    const sel = h("select", {"aria-label": f.label}, h("option", {value: ""}, f.label + ": todos"), ...vals.map(v => h("option", {value: v}, String(v).length > 60 ? String(v).slice(0, 57) + "…" : v)));
    sel.addEventListener("change", () => { f.valor = sel.value; page = 0; aplicar(); });
    barra.append(sel);
  });
  const bExp = h("button", {class: "btn sec", onclick: () => descargarCSV(o.nombreCsv, o.cols.map(c => c.t), vista.map(i => o.csvRow ? o.csvRow(rows[i]) : rows[i]))}, "Exportar vista (CSV)");
  barra.append(bExp);
  const tw = h("div", {class: "tw"}), pg = h("div", {class: "pg"});
  host.append(barra, tw, pg);

  function aplicar() {
    const ql = q.trim().toLowerCase();
    vista = [];
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      if (!filtros.every(f => f.valor === "" || String(f.get(r)) === f.valor)) continue;
      if (ql) { if (hay[i] === undefined) hay[i] = r.map(v => v == null ? "" : String(v)).join(" ").toLowerCase(); if (!hay[i].includes(ql)) continue; }
      vista.push(i);
    }
    if (sort) {
      const {k, dir} = sort;
      vista.sort((a, b) => { const x = rows[a][k], y = rows[b][k]; if (x == null) return 1; if (y == null) return -1;
        return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "es")) * dir; });
    }
    abierta = null; dibujar();
  }
  function dibujar() {
    const paginas = Math.max(1, Math.ceil(vista.length / size)); page = Math.min(page, paginas - 1);
    const t = h("table"), tr = h("tr");
    o.cols.forEach(c => {
      const th = h("th", {class: c.num ? "num" : "", title: "Ordenar"}, c.t + (sort && sort.k === c.k ? (sort.dir > 0 ? " ▲" : " ▼") : ""));
      th.addEventListener("click", () => { sort = sort && sort.k === c.k ? {k: c.k, dir: -sort.dir} : {k: c.k, dir: 1}; aplicar(); });
      tr.append(th);
    });
    t.append(h("thead", {}, tr));
    const tb = h("tbody");
    vista.slice(page * size, page * size + size).forEach(i => {
      const r = rows[i], row = h("tr", {class: "f" + (o.detalle ? " fx" : "")});
      o.cols.forEach(c => row.append(h("td", {class: c.num ? "num" : ""}, c.f ? c.f(r[c.k], r) : (r[c.k] ?? "—"))));
      tb.append(row);
      if (o.detalle) row.addEventListener("click", () => {
        if (abierta) { abierta.remove(); const mismo = abierta._de === row; abierta = null; if (mismo) return; }
        const d = h("tr", {class: "x"}, h("td", {colspan: o.cols.length}, o.detalle(r))); d._de = row; row.after(d); abierta = d;
      });
    });
    if (!vista.length) tb.append(h("tr", {}, h("td", {colspan: o.cols.length, class: "empty"}, "Ningún registro coincide con los filtros")));
    t.append(tb); tw.replaceChildren(t);
    const ini = vista.length ? page * size + 1 : 0, fin = Math.min(vista.length, page * size + size);
    pg.replaceChildren(h("span", {}, `${n(ini)}–${n(fin)} de ${n(vista.length)} registros` + (vista.length !== rows.length ? ` (filtrados de ${n(rows.length)})` : "")),
      h("span", {}, h("button", {class: "btn sec", disabled: page === 0, onclick: () => { page--; dibujar(); }}, "← Anterior"), "  Página " + (page + 1) + " de " + paginas + "  ",
        h("button", {class: "btn sec", disabled: page >= paginas - 1, onclick: () => { page++; dibujar(); }}, "Siguiente →")),
      h("span", {}, "Filas por página: ", (() => { const s = h("select", {}, ...[10, 25, 50, 100].map(v => h("option", {value: v, selected: v === size}, v))); s.addEventListener("change", () => { size = +s.value; page = 0; dibujar(); }); return s; })()));
  }
  let timer; inp.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { q = inp.value; page = 0; aplicar(); }, 180); });
  aplicar();
}

/* ---------- pestaña: RESUMEN ---------- */
function vistaResumen() {
  const s = $("#t-resumen"), rc = R.reconciliacion || {}, bd = R.bd || {};
  if (R.datos_demo) s.append(h("div", {class: "banner"}, "⚠ Estás viendo DATOS SINTÉTICOS de demostración, no los datasets oficiales."));
  const entrada = rc.filas_entrada || 0, desc = rc.descartadas_ing1 || 0, ok = rc.en_transacciones_limpias || 0, cq = rc.en_cuarentena_errores || 0;
  s.append(h("div", {class: "kpis"},
    kpi("Filas de entrada", n(entrada), "ambas fuentes"), kpi("Descartadas", n(desc), "duplicados exactos y cantidad ≤ 0"),
    kpi("Registros aptos", n(ok), "transacciones_limpias", "ok"), kpi("En cuarentena", n(cq), entrada ? n(100 * cq / entrada, 1) + " % de la entrada" : "", cq ? "bad" : ""),
    kpi("Monto cargado", corto(bd.monto_total_mxn || 0), "MXN · " + money(bd.monto_total_mxn), "acc")));
  const tot = Math.max(1, desc + ok + cq);
  const seg = (v, c, t) => h("div", {style: `width:${100 * v / tot}%;background:${c}`, title: t + ": " + n(v)}, v / tot > .06 ? n(v) : "");
  s.append(card("Recorrido de los registros", "Ningún registro se pierde: cada fila de entrada termina descartada con motivo, en la tabla limpia o en cuarentena.",
    h("div", {class: "flow"}, seg(desc, "#8791a5", "Descartadas"), seg(ok, "#17834a", "Aptas"), seg(cq, "#bf3a3a", "Cuarentena")),
    h("div", {class: "leg"}, h("span", {}, h("i", {style: "background:#8791a5"}), "Descartadas " + n(desc)), h("span", {}, h("i", {style: "background:#17834a"}), "Aptas " + n(ok)),
      h("span", {}, h("i", {style: "background:#bf3a3a"}), "Cuarentena " + n(cq)), chip(rc.cuadra ? "✓ La reconciliación cuadra" : "✗ NO cuadra", rc.cuadra ? "ok" : "bad"),
      chip("Estado: " + R.estado, R.estado === "OK" ? "ok" : "warn"), chip("integrity_check: " + (bd.integrity_check ?? "—"), bd.integrity_check === "ok" ? "ok" : "bad"))));
  const g = h("div", {class: "grid g2"});
  g.append(card("Ventas por mes", "Monto total en MXN de transacciones_limpias", columnas(AG.por_mes || [], i => corto(i.monto), mesCorto)));
  g.append(card("Ventas por categoría", "Monto total en MXN", barrasH(AG.por_categoria || [], i => money(i.monto))));
  s.append(g);
  const g2 = h("div", {class: "grid g3"});
  g2.append(card("Por canal", "Registros y monto", barrasH(AG.por_fuente || [], i => n(i.n) + " · " + corto(i.monto))));
  g2.append(card("Segmento de importe", "Bajo / Medio / Alto (terciles)", barrasH(AG.por_segmento || [], i => n(i.n), null)));
  g2.append(card("Causas de cuarentena", "Un registro puede fallar por varios campos", barrasH((D.motivos || []).slice(0, 6).map(m => ({label: m.campo + ": " + m.causa, value: m.registros})), null)));
  s.append(g2);
  if (R.errores && R.errores.length) s.append(card("Errores controlados durante la corrida", "", h("ul", {}, R.errores.map(e => h("li", {}, e)))));
  if (D.diagrama) s.append(h("div", {class: "card"}, h("details", {}, h("summary", {}, "Ver diagrama de flujo del pipeline"), h("p", {}), h("img", {src: D.diagrama, alt: "Diagrama de flujo del pipeline"}))));
}

/* ---------- pestaña: LIMPIAS ---------- */
function vistaLimpias() {
  const s = $("#t-limpias"), L = D.limpias, ix = Object.fromEntries(L.cols.map((c, i) => [c, i]));
  s.append(h("div", {class: "card"}, h("h2", {}, "transacciones_limpias"), h("p", {class: "sub"}, "Registros que cumplieron el esquema Pydantic y se cargaron a la base. Haz clic en un encabezado para ordenar."),
    (() => { const w = h("div"); tablaInteractiva(w, {
      rows: L.rows, nombreCsv: "transacciones_limpias_vista.csv", placeholder: "Buscar por venta, producto, cliente, correo…",
      filtros: [{label: "Canal", get: r => r[ix.fuente]}, {label: "Segmento", get: r => r[ix.segmento_monto]}, {label: "Categoría", get: r => r[ix.categoria]},
                {label: "Moneda original", get: r => r[ix.moneda_original]}, {label: "Mes", get: r => (r[ix.fecha] || "").slice(0, 7)},
                {label: "Sucursal", get: r => r[ix.sucursal]}, {label: "Estatus", get: r => r[ix.estatus_pedido]}],
      cols: [{k: ix.id_venta, t: "Venta", f: v => h("span", {class: "mono"}, v)}, {k: ix.fuente, t: "Canal", f: v => chip(v, v === "online" ? "acc" : "")}, {k: ix.fecha, t: "Fecha"},
             {k: ix.id_producto, t: "Producto", f: (v, r) => h("span", {title: r[ix.nombre_producto] || ""}, v + (r[ix.nombre_producto] ? " · " + r[ix.nombre_producto] : ""))},
             {k: ix.categoria, t: "Categoría"}, {k: ix.cantidad, t: "Cant.", num: true, f: v => n(v)}, {k: ix.precio_unitario, t: "Precio", num: true, f: v => money(v)},
             {k: ix.monto_total, t: "Monto MXN", num: true, f: v => money(v)}, {k: ix.moneda_original, t: "Orig.", f: v => chip(v || "—", v === "USD" ? "warn" : "")},
             {k: ix.segmento_monto, t: "Segmento", f: v => chip(v, v === "Alto" ? "ok" : v === "Bajo" ? "" : "acc")},
             {k: ix.id_cliente, t: "Cliente"}, {k: ix.email, t: "Correo", f: v => v === "ANONIMO" ? chip("ANONIMO") : v},
             {k: ix.metodo_pago, t: "Pago"}, {k: ix.sucursal, t: "Sucursal"},
             {k: ix.estatus_pedido, t: "Estatus", f: v => v ? chip(v, v === "CANCELADO" || v === "DEVUELTO" ? "bad" : v === "PENDIENTE" ? "warn" : "ok") : "—"},
             {k: ix.flags_ing1, t: "Banderas", f: v => chipsFlags(v)}],
      detalle: r => L.cols.map((c, i) => c + ": " + (r[i] ?? "—")).join("\n")}); return w; })()));
}

/* ---------- pestaña: CUARENTENA ---------- */
function vistaCuarentena() {
  const s = $("#t-cuarentena"), C = D.cuarentena;
  const campo = r => (String(r[4] || "").split(":")[0] || "(sin detalle)");
  s.append(h("div", {class: "grid g2"}, card("Causas más frecuentes", "Agrupadas por campo y motivo", barrasH((D.motivos || []).slice(0, 8).map(m => ({label: m.campo + ": " + m.causa, value: m.registros})))),
    card("Por etapa y canal", "Dónde se detuvo cada registro", barrasH((AG.cuarentena_por_etapa || []).map(i => ({label: i.label, value: i.value}))), h("p"), barrasH((AG.cuarentena_por_fuente || []).map(i => ({label: i.label, value: i.value}))))));
  s.append(h("div", {class: "card"}, h("h2", {}, "cuarentena_errores"), h("p", {class: "sub"}, "Registros rechazados con su motivo. Haz clic en una fila para ver el registro original completo."),
    (() => { const w = h("div"); tablaInteractiva(w, {
      rows: C.rows, nombreCsv: "cuarentena_errores_vista.csv", placeholder: "Buscar por venta, motivo, valor…",
      filtros: [{label: "Etapa", get: r => r[3]}, {label: "Canal", get: r => r[0]}, {label: "Campo con problema", get: campo}],
      cols: [{k: 1, t: "Venta", f: v => h("span", {class: "mono"}, v ?? "—")}, {k: 0, t: "Canal", f: v => chip(v || "—")}, {k: 2, t: "Fila origen", num: true},
             {k: 3, t: "Etapa", f: v => chip(v, v === "VALIDACION_PYDANTIC" ? "warn" : "bad")}, {k: 4, t: "Motivo de rechazo", f: v => h("span", {style: "white-space:normal"}, v)}],
      detalle: r => { try { return JSON.stringify(JSON.parse(r[5]), null, 2); } catch (e) { return String(r[5] ?? ""); } }}); return w; })()));
}

/* ---------- pestaña: CALIDAD (Ing. 1) ---------- */
function vistaCalidad() {
  const s = $("#t-calidad"), fuentes = Object.keys(R.fuentes || {});
  if (!fuentes.length) { s.append(card("Sin datos", "No hay información de calidad en esta corrida.")); return; }
  const cont = h("div"), pill = h("div", {class: "pill"});
  function pinta(f) {
    cont.replaceChildren(); const F = R.fuentes[f], I = D.ing1[f] || {};
    if (F.error) { cont.append(card("No se pudo procesar", F.error)); return; }
    const a = F.perfil_antes, d = F.perfil_despues, b = F.bitacora;
    const vs = (l, x, y, dec) => h("div", {}, h("div", {class: "sub", style: "margin:0"}, l), h("div", {class: "vs"}, h("div", {class: "a"}, n(x, dec)), h("div", {class: "arrow"}, "→"), h("div", {class: "d"}, n(y, dec))));
    cont.append(card("Fase 1 · Antes vs. después", "Diagnóstico inicial contra el resultado de las fases 2 y 3",
      h("div", {class: "grid g3"}, vs("Filas", a.filas, d.filas), vs("Duplicados exactos", a.duplicados_exactos, d.duplicados_exactos), vs("Filas con algún faltante", a.filas_con_algun_faltante, d.filas_con_algun_faltante),
        vs("Completitud global (%)", a.completitud_global_pct, d.completitud_global_pct, 2))));
    const pa = (I.perfil_antes || []), pd = Object.fromEntries((I.perfil_despues || []).map(x => [x.columna, x]));
    cont.append(card("Faltantes por columna", "Incluye textos como N/A, - o s/d (pseudo-nulos)",
      barrasH(pa.filter(x => x.faltantes_total > 0).map(x => ({label: x.columna, value: x.pct_faltantes})), i => n(i.value, 2) + " %", 100),
      h("p", {class: "sub"}, "Después de la limpieza, todas las columnas quedan sin faltantes salvo llaves y fechas, que nunca se imputan."),
      tablaSimple([{k: "columna", t: "Columna"}, {k: "tipo_inferido", t: "Tipo inferido"}, {k: "nulos", t: "Nulos", num: true, f: v => n(v)}, {k: "pseudo_nulos", t: "Pseudo-nulos", num: true, f: v => n(v)},
        {k: "pct_faltantes", t: "% faltante", num: true, f: v => n(v, 2)}, {k: "unicos", t: "Únicos", num: true, f: v => n(v)}, {k: "nota", t: "Nota"}], pa)));
    const fc = F.faltantes_clasificacion || [];
    cont.append(card("Fase 2 · Mecanismo de ausencia (MCAR / MAR)", "Mann-Whitney U (numéricas) o chi-cuadrada (categóricas) con corrección de Bonferroni, α = 0.05. MNAR no es verificable con los datos observados.",
      fc.length ? tablaSimple([{k: "columna", t: "Columna"}, {k: "faltantes", t: "Faltantes", num: true, f: v => n(v)}, {k: "pct_faltantes", t: "%", num: true, f: v => n(v, 2)},
        {k: "clasificacion", t: "Clasificación", f: v => chip(v, v === "MAR" ? "warn" : v === "MCAR" ? "ok" : "")}, {k: "variable_asociada", t: "Depende de"}, {k: "p_ajustado", t: "p ajustado", num: true, f: v => v == null ? "—" : n(v, 4)},
        {k: "recalculables_desde_otras_columnas", t: "Recalculables", num: true, f: v => n(v)}, {k: "estrategia", t: "Estrategia"}], fc) : h("div", {class: "empty"}, "No hubo columnas con faltantes")));
    if (I.estadisticos && I.estadisticos.length) cont.append(card("Variables numéricas · antes vs. después", "La media se infla con bots y compras gigantes; por eso se imputa con la mediana.",
      tablaSimple(["columna", "momento", "n", "media", "mediana", "desv_est", "minimo", "p95", "maximo", "asimetria"].map((k, i) => ({k, t: k.replace("_", " "), num: i > 1, f: i > 1 ? (v => n(v, k === "n" ? 0 : 2)) : (k === "momento" ? (v => chip(v, v === "antes" ? "warn" : "ok")) : null)})), I.estadisticos)));
    if (I.umbrales && I.umbrales.length) cont.append(card("Fase 3 · Outliers con IQR (factor " + n(R.parametros.factor_iqr, 1) + ")", "Capping/winsorizing: se recorta al límite, no se elimina la venta. Los importes se calculan por moneda.",
      tablaSimple([{k: "columna", t: "Columna"}, {k: "grupo", t: "Grupo"}, {k: "n", t: "n", num: true, f: v => n(v)}, {k: "q1", t: "Q1", num: true, f: v => n(v, 2)}, {k: "q3", t: "Q3", num: true, f: v => n(v, 2)},
        {k: "lim_inf", t: "Límite inf.", num: true, f: v => n(v, 2)}, {k: "lim_sup", t: "Límite sup.", num: true, f: v => n(v, 2)}, {k: "capeados_sup", t: "Recortados (sup.)", num: true, f: v => n(v)},
        {k: "accion", t: "Acción", f: v => chip(v, v === "recortado" ? "ok" : "")}], I.umbrales), I.grafica ? h("p", {}, h("img", {src: I.grafica, alt: "Boxplots antes y después"})) : null));
    const inter = Object.entries(b).filter(([k, v]) => typeof v === "number" && !["filas_entrada", "filas_salida"].includes(k) && v > 0).map(([k, v]) => ({label: k.replace(/_/g, " "), value: v}));
    cont.append(card("Intervenciones registradas", "Cada una queda marcada fila por fila en la columna flags_ing1", barrasH(inter)));
    if (b.advertencias && b.advertencias.length) cont.append(card("Advertencias", "", h("ul", {}, b.advertencias.map(x => h("li", {}, x)))));
  }
  fuentes.forEach((f, i) => pill.append(h("button", {class: i ? "" : "on", onclick: e => { [...pill.children].forEach(x => x.classList.remove("on")); e.target.classList.add("on"); pinta(f); }}, f === "fisica" ? "Tienda física" : f === "online" ? "Tienda online" : f)));
  s.append(pill, cont); pinta(fuentes[0]);
}

/* ---------- pestaña: INTEGRACIÓN (Ing. 2) ---------- */
function vistaIntegracion() {
  const s = $("#t-integracion"), I = R.integracion || {}, f = I.fechas || {}, m = I.moneda || {}, c = I.catalogo || {}, cat = R.catalogo || {}, mm = I.minmax || {}, sg = I.segmentos || {};
  s.append(h("div", {class: "kpis"}, kpi("Desde DD/MM/AAAA", n(f.dmy), "convertidas a ISO"), kpi("Desde epoch", n(f.epoch), "timestamp numérico a ISO"), kpi("Ya en ISO", n(f.iso), "sin cambios"), kpi("Fechas inválidas", n((f.imposibles || 0) + (f.formato_no_reconocido || 0)), "a nulo → cuarentena", (f.imposibles || 0) ? "bad" : "ok"),
    kpi("USD → MXN", n(m.filas_usd_convertidas), "paridad $" + n(m.tasa_usd_mxn, 2), "acc"), kpi("Huérfanos", n(c.huerfanos), "sin producto en el catálogo", c.huerfanos ? "bad" : "ok"), kpi("Productos en catálogo", n(cat.registros_salida), "tras limpiar llaves")));
  const g = h("div", {class: "grid g2"});
  g.append(card("Fase 5 · Min-Max y segmentos", `monto_total (MXN) se escala a [0, 1]: mínimo ${money(mm.minimo)} y máximo ${money(mm.maximo)}. Segmentos por ${sg.metodo || "cuantiles"}: Bajo hasta ${money(sg.corte_bajo_medio)}, Medio hasta ${money(sg.corte_medio_alto)}, Alto por encima.`,
    barrasH(Object.entries(sg.conteo || {}).map(([k, v]) => ({label: k, value: v})))));
  g.append(card("Distribución del importe (MXN)", "Histograma de transacciones_limpias", columnas(AG.hist_monto || [], null, null)));
  s.append(g);
  const g2 = h("div", {class: "grid g2"});
  g2.append(card("Top 10 productos por monto", "", barrasH(AG.top_productos || [], i => money(i.monto))));
  g2.append(card("Método de pago", "Registros y monto", barrasH(AG.por_metodo_pago || [], i => n(i.n) + " · " + corto(i.monto))));
  s.append(g2);
  const g3 = h("div", {class: "grid g2"});
  g3.append(card("Sucursal (tienda física)", "Monto en MXN por punto de venta", barrasH(AG.por_sucursal || [], i => n(i.n) + " · " + corto(i.monto))));
  g3.append(card("Estatus del pedido (tienda online)", "Se conservan todos los estatus: filtra CANCELADO y DEVUELTO antes de sumar ingresos", barrasH(AG.por_estatus || [], i => n(i.n) + " · " + corto(i.monto))));
  s.append(g3);
  s.append(card("Moneda original", "Las ventas en USD se convirtieron a MXN antes de cargarse", barrasH(AG.por_moneda_original || [], i => n(i.n) + " registros")));
}

/* ---------- pestaña: ESTRÉS ---------- */
function vistaEstres() {
  const s = $("#t-estres"), E = D.estres || [];
  if (!E.length) { s.append(card("Sin resultados de estrés", "Ejecuta python3 test_estres.py y vuelve a generar el visor.")); return; }
  const ok = E.filter(e => e.resultado === "PASA").length;
  s.append(h("div", {class: "kpis"}, kpi("Escenarios", n(E.length), "datos corruptos inyectados"), kpi("Aprobados", n(ok), ok === E.length ? "sin excepciones no controladas" : "revisar fallas", ok === E.length ? "ok" : "bad"),
    kpi("Reconciliación", E.every(e => e.reconciliacion_cuadra !== false) ? "✓" : "✗", "entrada = descartadas + limpias + cuarentena", "ok")));
  s.append(card("Batería de pruebas de estrés", "Cada escenario corre el pipeline completo con datos dañados. Aprobar = no hubo crash, las filas cuadran y la base quedó íntegra.",
    tablaSimple([{k: "escenario", t: "Escenario"}, {k: "descripcion", t: "Qué se corrompió"}, {k: "resultado", t: "Resultado", f: v => chip(v, v === "PASA" ? "ok" : "bad")},
      {k: "estado_pipeline", t: "Estado", f: v => chip(v, v === "OK" ? "ok" : "warn")}, {k: "filas_entrada", t: "Entrada", num: true, f: v => n(v)}, {k: "en_limpias", t: "Limpias", num: true, f: v => n(v)},
      {k: "en_cuarentena", t: "Cuarentena", num: true, f: v => n(v)}, {k: "errores_controlados", t: "Error controlado"}], E)));
}

/* ---------- arranque ---------- */
const TABS = [["resumen", "Resumen", vistaResumen], ["limpias", "Transacciones limpias", vistaLimpias], ["cuarentena", "Cuarentena", vistaCuarentena],
  ["calidad", "Calidad (Ing. 1)", vistaCalidad], ["integracion", "Integración (Ing. 2)", vistaIntegracion], ["estres", "Pruebas de estrés", vistaEstres]];
const hechas = {};
function ir(id) {
  if (!TABS.some(t => t[0] === id)) id = "resumen";
  TABS.forEach(([k, , fn]) => { $("#t-" + k).classList.toggle("on", k === id); });
  [...$("#nav").children].forEach(b => b.classList.toggle("on", b.dataset.t === id));
  if (!hechas[id]) { hechas[id] = true; try { TABS.find(t => t[0] === id)[2](); } catch (e) { $("#t-" + id).append(card("No se pudo dibujar esta sección", String(e))); console.error(e); } }
  history.replaceState(null, "", "#" + id);
}
const bc = (R.reconciliacion || {});
$("#sub").textContent = "Generado el " + D.generado + " · " + n(bc.filas_entrada) + " filas de entrada · estado " + R.estado + (R.duracion_segundos != null ? " · " + n(R.duracion_segundos, 1) + " s" : "");
TABS.forEach(([k, t]) => $("#nav").append(h("button", {"data-t": k, onclick: () => ir(k)}, t + (k === "cuarentena" && bc.en_cuarentena_errores ? "  (" + n(bc.en_cuarentena_errores) + ")" : ""))));
ir(location.hash.slice(1) || "resumen");
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
