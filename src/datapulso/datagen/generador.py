"""Generador de datos de prueba con semilla.

Estructura sembrada (la verdad que los hitos 2 y 4 deben poder recuperar):
- Cada cliente tiene un riesgo latente z. La probabilidad mensual de baja depende de z y de sus atributos.
- Antes de irse, el cliente usa menos, reclama más y paga más tarde: ESA es la señal que el motor de puntaje debe encontrar.
- Algunos clientes nunca activan (no_activa) y se van antes.
- La verdad (z, baja, no_activa) se guarda aparte en verdad_sembrada.json, NUNCA en las tablas del producto.
- El efecto de una acción (para el hito 4) NO está aplicado en este historial: lo aplica el simulador de resultados.

Misma semilla => mismos datos, siempre.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
from .industrias import Industria, INDUSTRIAS, nombre_col, plantilla_mapeos

CORTE_DEFECTO = date(2026, 10, 31)
MESES_HISTORIA = 24
_D = np.timedelta64(1, "D")


@dataclass
class Dataset:
    ind: Industria
    corte: date
    entidades: pd.DataFrame
    eventos: pd.DataFrame
    pagos: pd.DataFrame
    verdad: dict


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def generar(industria: str, semilla: int = 7, n_clientes: int | None = None, corte: date = CORTE_DEFECTO) -> Dataset:
    ind = INDUSTRIAS[industria]
    n = n_clientes or ind.n_clientes
    rng = np.random.default_rng(semilla)
    prefijo = ind.nombre[0].upper()

    dias_hist = MESES_HISTORIA * 30
    inicio = np.datetime64(corte, "D") - dias_hist * _D
    corte_d = np.datetime64(corte, "D")

    # ---- atributos y riesgo latente
    z = rng.normal(size=n)
    efecto = np.zeros(n)
    attrs: dict[str, np.ndarray] = {}
    for nombre, opts in ind.atributos.items():
        vals = [o[0] for o in opts]
        idx = rng.choice(len(opts), size=n, p=np.array([o[1] for o in opts]) / sum(o[1] for o in opts))
        attrs[nombre] = np.array(vals, dtype=object)[idx]
        efecto += np.array([o[2] for o in opts])[idx]
    no_activa = rng.random(n) < _sigmoid(-1.7 + 0.6 * z)

    h0 = ind.hazard_base_mensual
    logit = np.log(h0 / (1 - h0)) + 0.9 * z + efecto + 0.9 * no_activa
    hazard = np.clip(_sigmoid(logit), 0.002, 0.6)
    T = rng.geometric(hazard)  # ciclo (de 30 días) en el que se da de baja

    alta = inicio + (rng.beta(1.4, 1.0, n) * (dias_hist - 7)).astype(int) * _D
    baja_dia = alta + (T * 30 + rng.integers(-8, 8, n)) * _D
    se_fue = baja_dia <= corte_d
    fin = np.where(se_fue, baja_dia, corte_d)  # último día con actividad posible

    # ---- empresas (jerarquía B2B2C)
    n_emp = max(1, int(n * ind.pct_empresas / 100))
    ids_emp = [f"{prefijo}EMP{j:04d}" for j in range(1, n_emp + 1)]
    emp_alta = inicio - rng.integers(30, 400, n_emp) * _D
    pertenece = rng.random(n) < ind.pct_con_empresa
    empresa_de = np.where(pertenece, np.array(ids_emp, dtype=object)[rng.integers(0, n_emp, n)], None)

    ids = [f"{prefijo}{i:06d}" for i in range(1, n + 1)]
    ev_filas: list[pd.DataFrame] = []
    pg_filas: list[pd.DataFrame] = []
    attr_cuota, tabla_cuota = ind.cuota_por_atributo
    cuota_base = np.array([tabla_cuota[v] for v in attrs[attr_cuota]], dtype=float) * np.exp(rng.normal(0, 0.12, n))
    za = rng.normal(size=n)  # intensidad de uso propia del cliente, independiente del riesgo

    for i in range(n):
        a, f = alta[i], fin[i]
        nc = int(np.ceil(((f - a) / _D) / 30)) if f > a else 1
        nc = max(nc, 1)
        c = np.arange(nc)
        starts = a + c * 30 * _D
        ends = np.minimum(starts + 30 * _D, f + _D)
        span = np.maximum(((ends - starts) / _D).astype(int), 1)
        k = (T[i] - c) if se_fue[i] else np.full(nc, 99)  # ciclos que faltan para la baja (1 = el último)
        cae = np.where(k >= 4, 1.0, np.where(k == 3, 0.75, np.where(k == 2, 0.5, 0.25)))
        sube = np.where(k <= 3, 2.5, 1.0)
        arranque = np.where((c < 2) & no_activa[i], 0.06, 1.0)

        for te in ind.eventos:
            lam = te.tasa_mensual * np.exp(te.pendiente_riesgo * z[i] + 0.35 * za[i])
            lam = lam * (cae if te.respuesta_pre_baja == "cae" else sube if te.respuesta_pre_baja == "sube" else 1.0)
            if te.respuesta_pre_baja == "cae":
                lam = lam * arranque
            cnt = rng.poisson(lam)
            tot = int(cnt.sum())
            if not tot:
                continue
            cs = np.repeat(c, cnt)
            dias = starts[cs] + (rng.random(tot) * span[cs]).astype(int) * _D
            segs = rng.integers(7 * 3600, 23 * 3600, tot)
            ts = dias.astype("datetime64[s]") + segs.astype("timedelta64[s]")
            monto = (np.round(rng.lognormal(np.log(te.monto_mediana), 0.6, tot), 2)
                     if te.monto_mediana else np.full(tot, np.nan))
            canal = np.array(te.canales, dtype=object)[rng.integers(0, len(te.canales), tot)] if te.canales else None
            ev_filas.append(pd.DataFrame({"entidad": ids[i], "ts": ts, "tipo": te.tipo, "monto": monto, "canal": canal}))

        # ---- facturación / cuotas: una por ciclo
        emis = starts
        keep = emis <= corte_d
        if keep.any():
            c2, emis = c[keep], emis[keep]
            venc = emis + ind.dias_vencimiento * _D
            k2 = k[keep]
            p_tarde = _sigmoid(ind.mora_base_logit + 0.8 * z[i] + 1.4 * (k2 <= 2) + 0.3 * no_activa[i])
            p_impago = np.where(k2 == 1, 0.5, np.where(k2 == 2, 0.2, 0.015))
            tarde = rng.random(len(c2)) < p_tarde
            demora = np.where(tarde, 1 + rng.geometric(0.12, len(c2)), -rng.integers(0, 8, len(c2)))
            pago = venc + demora * _D
            impago = rng.random(len(c2)) < p_impago
            pago_ok = (~impago) & (pago <= corte_d)
            monto = np.round(np.full(len(c2), cuota_base[i]), 2)
            parcial = pago_ok & (rng.random(len(c2)) < 0.05)
            pagado = np.where(parcial, np.round(monto * rng.uniform(0.5, 0.9, len(c2)), 2), monto)
            pg_filas.append(pd.DataFrame({
                "comprobante": [f"{prefijo}F{i + 1:06d}-{x:03d}" for x in c2],
                "entidad": ids[i], "fecha_emision": emis, "fecha_vencimiento": venc, "monto": monto,
                "fecha_pago": np.where(pago_ok, pago, np.datetime64("NaT", "D")),
                "monto_pagado": np.where(pago_ok, pagado, np.nan)}))

    # ---- armar tablas
    cols_attr = list(attrs.keys())
    pers = pd.DataFrame({"id_externo": ids, "tipo": "persona", "empresa": empresa_de, "fecha_alta": alta.astype("datetime64[D]"),
                         "fecha_baja": np.where(se_fue, baja_dia, np.datetime64("NaT", "D")).astype("datetime64[D]"),
                         "estado": np.where(se_fue, "baja", "activo")})
    for nm in cols_attr:
        pers[nm] = attrs[nm]
    emp = pd.DataFrame({"id_externo": ids_emp, "tipo": "empresa", "empresa": None, "fecha_alta": emp_alta.astype("datetime64[D]"),
                        "fecha_baja": np.datetime64("NaT", "D"), "estado": "activo"})
    for nm in cols_attr:
        emp[nm] = None
    ent = pd.concat([emp, pers], ignore_index=True)
    ent["modificado_en"] = (pd.to_datetime(ent["fecha_baja"].fillna(ent["fecha_alta"])) + pd.Timedelta(hours=12))

    ev = pd.concat(ev_filas, ignore_index=True).sort_values("ts", kind="stable").reset_index(drop=True)
    ev["id_externo"] = [f"{prefijo}E{j:08d}" for j in range(1, len(ev) + 1)]
    ev["monto"] = ev["monto"].astype(float)
    ev["modificado_en"] = ev["ts"]

    pg = pd.concat(pg_filas, ignore_index=True)
    pg["fecha_emision"] = pd.to_datetime(pg["fecha_emision"]).dt.date
    pg["fecha_vencimiento"] = pd.to_datetime(pg["fecha_vencimiento"]).dt.date
    pg["fecha_pago"] = pd.to_datetime(pg["fecha_pago"]).dt.date
    pg["modificado_en"] = [datetime.combine(fp, datetime.min.time()) + timedelta(hours=18) if pd.notna(fp)
                           else datetime.combine(fe, datetime.min.time()) + timedelta(minutes=5)
                           for fp, fe in zip(pg["fecha_pago"], pg["fecha_emision"])]
    ent["fecha_alta"] = pd.to_datetime(ent["fecha_alta"]).dt.date
    ent["fecha_baja"] = pd.to_datetime(ent["fecha_baja"]).dt.date

    verdad = {
        "industria": ind.nombre, "semilla": semilla, "corte": str(corte), "n_clientes": n,
        "nota": "Verdad sembrada para validar el motor (hito 2) y la medición (hito 4). NO se carga en el producto.",
        "efecto_accion_sembrado": {"descripcion": "Reducción absoluta de la probabilidad de baja en 60 días para tratados "
                                                  "que reciben la acción. Lo aplica el simulador de resultados (hito 4), "
                                                  "no este historial.", "efecto_absoluto": -0.08},
        "clientes": {ids[i]: {"riesgo_latente": round(float(z[i]), 4), "se_dio_de_baja": bool(se_fue[i]),
                              "baja": str(baja_dia[i]) if se_fue[i] else None, "no_activa": bool(no_activa[i])}
                     for i in range(n)},
    }
    return Dataset(ind, corte, ent, ev, pg, verdad)


# ---------------------------------------------------------------- vistas en el tiempo
def _fin_dia(d: date) -> datetime:
    return datetime.combine(d, datetime.max.time())


def snapshot_a(ds: Dataset, fecha: date) -> Dataset:
    """Cómo se veía la base del cliente al final de `fecha` (lo que hubiera leído una sincronización ese día)."""
    lim = _fin_dia(fecha)
    e = ds.entidades[pd.to_datetime(ds.entidades["fecha_alta"]) <= pd.Timestamp(fecha)].copy()
    revertir = e["fecha_baja"].notna() & (pd.to_datetime(e["fecha_baja"]) > pd.Timestamp(fecha))
    e.loc[revertir, "fecha_baja"] = None
    e.loc[revertir, "estado"] = "activo"
    e.loc[revertir, "modificado_en"] = pd.to_datetime(e.loc[revertir, "fecha_alta"]) + pd.Timedelta(hours=12)
    ev = ds.eventos[ds.eventos["ts"] <= pd.Timestamp(lim)].copy()
    p = ds.pagos[pd.to_datetime(ds.pagos["fecha_emision"]) <= pd.Timestamp(fecha)].copy()
    rev = p["fecha_pago"].notna() & (pd.to_datetime(p["fecha_pago"]) > pd.Timestamp(fecha))
    p.loc[rev, "fecha_pago"] = None
    p.loc[rev, "monto_pagado"] = np.nan
    p.loc[rev, "modificado_en"] = [datetime.combine(x, datetime.min.time()) + timedelta(minutes=5) for x in p.loc[rev, "fecha_emision"]]
    return Dataset(ds.ind, fecha, e, ev, p, ds.verdad)


def delta_desde(ds: Dataset, fecha: date, hasta: date | None = None) -> Dataset:
    """Lo que cambió en la base del cliente después de `fecha` (altas, eventos nuevos, pagos actualizados, bajas)."""
    lim = pd.Timestamp(_fin_dia(fecha))
    full = snapshot_a(ds, hasta) if hasta else ds
    e = full.entidades[pd.to_datetime(full.entidades["modificado_en"]) > lim].copy()
    ev = full.eventos[full.eventos["modificado_en"] > lim].copy()
    p = full.pagos[pd.to_datetime(full.pagos["modificado_en"]) > lim].copy()
    return Dataset(ds.ind, hasta or ds.corte, e, ev, p, ds.verdad)


# ---------------------------------------------------------------- exportación
def tabla_cliente(ds: Dataset, tabla: str) -> pd.DataFrame:
    """Tabla con los nombres de columna 'del cliente' (los que verá el mapeo)."""
    ind = ds.ind
    df = {"entidades": ds.entidades, "eventos": ds.eventos, "pagos": ds.pagos}[tabla].copy()
    return df.rename(columns=ind.renombres[tabla])


_COLS_FECHA = {"fecha_alta", "fecha_baja", "fecha_emision", "fecha_vencimiento", "fecha_pago"}
_COLS_TS = {"ts", "modificado_en"}


def formatear(df: pd.DataFrame, estilo: str) -> pd.DataFrame:
    """Convierte fechas y fecha-hora a texto según el estilo (usado para CSV; la base conserva los tipos)."""
    df = df.copy()
    ff, ft = ("%d/%m/%Y", "%d/%m/%Y %H:%M:%S") if estilo == "ar" else ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S")
    for col in df.columns:
        if col in _COLS_FECHA:
            df[col] = df[col].map(lambda d: d.strftime(ff) if pd.notna(d) else "")
        elif col in _COLS_TS:
            df[col] = pd.to_datetime(df[col]).dt.strftime(ft)
    return df


def exportar_csv(ds: Dataset, carpeta: str | Path, estilo: str = "iso") -> dict:
    """Escribe los 3 CSV. estilo 'iso': coma y AAAA-MM-DD. estilo 'ar': punto y coma, DD/MM/AAAA y coma decimal."""
    out = Path(carpeta)
    out.mkdir(parents=True, exist_ok=True)
    rutas = {}
    for tabla in ("entidades", "eventos", "pagos"):
        df = {"entidades": ds.entidades, "eventos": ds.eventos, "pagos": ds.pagos}[tabla]
        df = formatear(df, estilo).rename(columns=ds.ind.renombres[tabla])
        ruta = out / f"{ds.ind.tablas[tabla]}.csv"
        df.to_csv(ruta, index=False, sep=";" if estilo == "ar" else ",", decimal="," if estilo == "ar" else ".",
                  encoding="utf-8")
        rutas[tabla] = str(ruta)
    (out / "verdad_sembrada.json").write_text(json.dumps(ds.verdad, ensure_ascii=False), encoding="utf-8")
    rutas["verdad"] = str(out / "verdad_sembrada.json")
    return rutas


def mapeo_para(ind: Industria, tabla: str, estilo: str = "iso", desde_bd: bool = False) -> dict:
    m = plantilla_mapeos(ind)[tabla]
    mm = {**m, "columnas": dict(m["columnas"])}
    if estilo == "ar" and not desde_bd:
        mm.update(formato_fecha="%d/%m/%Y", formato_ts="%d/%m/%Y %H:%M:%S", separador_decimal=",")
    return mm
