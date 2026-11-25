"""Variables por cliente a una fecha de corte, calculadas desde la base.

Regla de oro (sin fuga de información): una variable "a la fecha c" usa solo lo que se sabía al final del día c
en la zona horaria del tenant: eventos con ts anterior al fin de ese día, comprobantes emitidos hasta c y pagos
registrados hasta c (un pago posterior a c cuenta como impago a la fecha c). Lo prueba
`tests/test_puntaje.py::test_variables_sin_fuga_de_informacion`.

Excepción conocida: los atributos de la entidad (plan, ciudad, ...) se toman como están hoy porque la base no guarda
su historia. Si un cliente cambia de plan justo antes de irse, eso filtra información (decisión pendiente en la guía).
`estado_externo` no se usa nunca: suele decir directamente "baja".

Variables (nombre -> significado; las de evento se repiten por tipo de evento):
  antiguedad_dias       días desde el alta
  tiene_empresa         1 si pertenece a una empresa (solo si el tenant tiene jerarquía)
  dias_sin_eventos      días desde el último evento de cualquier tipo (tope 365)
  eventos_90d, monto_90d  totales de los últimos 90 días (solo para la regla simple: el modelo usa el detalle por
                        tipo, y sumar los totales lo vuelve colineal y los drivers confunden)
  n90__<tipo>           eventos del tipo en los últimos 90 días
  sin90__<tipo>         1 si no hubo ningún evento del tipo en 90 días (el salto de "poco" a "nada" no es lineal)
  tendencia__<tipo>     (eventos últimos 30 días + 1) / (promedio mensual de los 60 anteriores + 1); <1 = cae
  ticket90__<tipo>      monto promedio por evento del tipo en 90 días (solo tipos con monto; el total sería
                        casi colineal con la cantidad). Vacío si no hubo eventos del tipo.
  pct_tarde_180d        fracción de comprobantes (vencidos en 180 días y fuera de la gracia) pagados tarde o impagos
  atraso_prom_180d      días de atraso promedio de esos comprobantes
  impagas_vencidas      comprobantes vencidos (y fuera de la gracia) impagos a la fecha
  attr__<atributo>      atributo categórico de la entidad (texto)
"""
from __future__ import annotations
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

# Subir este número cada vez que cambie el cálculo o el significado de una variable: los modelos guardan la versión
# con la que se entrenaron y el proceso nocturno reentrena los que quedaron desactualizados.
VERSION_VARIABLES = 4
MAX_TIPOS = 6            # tipos de evento con variables propias (los más frecuentes)
MAX_NIVELES_ATRIBUTO = 12
TOPE_DIAS = 365


def zona(conn) -> str:
    return conn.execute("SELECT zona_horaria FROM tenants").fetchone()["zona_horaria"]


def inicio_dia(d: date, tz: str) -> datetime:
    """Comienzo del día `d` en la zona del tenant (timestamptz)."""
    return datetime.combine(d, time(0), ZoneInfo(tz))


def fin_dia(d: date, tz: str) -> datetime:
    """Límite exclusivo del día `d`: comienzo del día siguiente."""
    return inicio_dia(d + timedelta(days=1), tz)


def descubrir(conn, gracia: int, excluir_atributos: tuple[str, ...] = ()) -> dict:
    """Qué variables existen para este tenant (tipos de evento, atributos, pagos). Se guarda con el modelo para que
    el puntaje de mañana use exactamente las mismas variables que el entrenamiento.
    `excluir_atributos`: atributos que no son comportamiento y no deben entrar al modelo (el consentimiento de
    contacto de la política de contacto es un permiso, no una señal de riesgo)."""
    tipos = conn.execute("""SELECT tipo, count(*) AS n, count(monto) AS con_monto FROM eventos
                            GROUP BY tipo HAVING count(*) >= 20 ORDER BY count(*) DESC, tipo""").fetchall()
    tipos = tipos[:MAX_TIPOS]
    attrs = conn.execute(
        """WITH kv AS (SELECT e.id, a.key, a.value FROM entidades e, jsonb_each(e.atributos) a
                       WHERE e.tipo = 'persona' AND jsonb_typeof(a.value) = 'string'),
                tot AS (SELECT count(*) AS n FROM entidades WHERE tipo = 'persona')
           SELECT key, count(DISTINCT value) AS niveles, count(*)::float / NULLIF((SELECT n FROM tot), 0) AS cobertura
           FROM kv GROUP BY key ORDER BY key""").fetchall()
    hay = conn.execute("""SELECT EXISTS (SELECT 1 FROM pagos) AS pagos,
                                 EXISTS (SELECT 1 FROM entidades WHERE parent_id IS NOT NULL) AS empresa,
                                 EXISTS (SELECT 1 FROM entidades WHERE tipo='persona' AND fecha_baja IS NOT NULL) AS bajas""").fetchone()
    return {
        "tipos": [r["tipo"] for r in tipos],
        "tipos_monto": [r["tipo"] for r in tipos if r["con_monto"] >= 0.5 * r["n"]],
        "atributos": [r["key"] for r in attrs
                      if 2 <= r["niveles"] <= MAX_NIVELES_ATRIBUTO and (r["cobertura"] or 0) >= 0.5
                      and r["key"] not in excluir_atributos],
        "pagos": bool(hay["pagos"]), "empresa": bool(hay["empresa"]), "hay_bajas": bool(hay["bajas"]),
        "gracia": gracia,
    }


def base_vigentes(conn, c: date) -> pd.DataFrame:
    """Personas dadas de alta hasta c y no dadas de baja a esa fecha."""
    rows = conn.execute("""SELECT id AS entidad_id, id_externo, fecha_alta, parent_id IS NOT NULL AS tiene_empresa, atributos
                           FROM entidades WHERE tipo = 'persona' AND fecha_alta <= %(c)s
                             AND (fecha_baja IS NULL OR fecha_baja > %(c)s)
                           ORDER BY id_externo""", {"c": c}).fetchall()
    df = pd.DataFrame(rows, columns=["entidad_id", "id_externo", "fecha_alta", "tiene_empresa", "atributos"])
    return df.set_index("entidad_id")


def calcular(conn, c: date, spec: dict, base: pd.DataFrame | None = None) -> pd.DataFrame:
    """Matriz de variables crudas a la fecha c para las entidades de `base` (por defecto, las vigentes a c)."""
    tz = zona(conn)
    base = base_vigentes(conn, c) if base is None else base
    X = pd.DataFrame(index=base.index)
    if base.empty:
        return X
    lim = fin_dia(c, tz)
    X["antiguedad_dias"] = [(c - d).days for d in base["fecha_alta"]]
    if spec["empresa"]:
        X["tiene_empresa"] = base["tiene_empresa"].astype(float)

    ev = pd.DataFrame(conn.execute(
        """SELECT entidad_id, tipo,
                  count(*) FILTER (WHERE ts >= %(lim)s - interval '30 days') AS n30,
                  count(*) FILTER (WHERE ts >= %(lim)s - interval '90 days' AND ts < %(lim)s - interval '30 days') AS n_31_90,
                  count(*) FILTER (WHERE ts >= %(lim)s - interval '90 days') AS n90,
                  COALESCE(sum(monto) FILTER (WHERE ts >= %(lim)s - interval '90 days'), 0)::float AS m90,
                  extract(epoch FROM %(lim)s - max(ts)) / 86400.0 AS dias_ult
           FROM eventos WHERE ts < %(lim)s AND ts >= %(lim)s - interval '365 days'
           GROUP BY entidad_id, tipo""", {"lim": lim}).fetchall(),
        columns=["entidad_id", "tipo", "n30", "n_31_90", "n90", "m90", "dias_ult"])
    if not ev.empty:
        ev["dias_ult"] = ev["dias_ult"].astype(float)
    tot = ev.groupby("entidad_id").agg(n90=("n90", "sum"), m90=("m90", "sum"), dias_ult=("dias_ult", "min"))
    X["dias_sin_eventos"] = tot["dias_ult"].reindex(X.index).fillna(TOPE_DIAS).clip(upper=TOPE_DIAS).astype(float)
    X["eventos_90d"] = tot["n90"].reindex(X.index).fillna(0).astype(float)
    if spec["tipos_monto"]:
        X["monto_90d"] = tot["m90"].reindex(X.index).fillna(0).astype(float)
    for tipo in spec["tipos"]:
        g = ev[ev["tipo"] == tipo].set_index("entidad_id").reindex(X.index)
        n30, nprev, n90 = (g[k].fillna(0).astype(float) for k in ("n30", "n_31_90", "n90"))
        X[f"n90__{tipo}"] = n90
        X[f"sin90__{tipo}"] = (n90 == 0).astype(float)
        X[f"tendencia__{tipo}"] = (n30 + 1.0) / (nprev / 2.0 + 1.0)
        if tipo in spec["tipos_monto"]:
            # sin eventos el ticket no está definido: queda vacío y el modelo lo trata como neutro
            X[f"ticket90__{tipo}"] = (g["m90"].fillna(0).astype(float) / n90).where(n90 > 0)

    if spec["pagos"]:
        g = spec["gracia"]
        pg = pd.DataFrame(conn.execute(
            """SELECT entidad_id,
                      count(*) FILTER (WHERE v) AS n_venc,
                      count(*) FILTER (WHERE v AND (fecha_pago IS NULL OR fecha_pago > %(c)s
                                                    OR fecha_pago > fecha_vencimiento + %(g)s)) AS n_tarde,
                      COALESCE(avg(GREATEST(0, (CASE WHEN fecha_pago IS NOT NULL AND fecha_pago <= %(c)s THEN fecha_pago
                                                     ELSE %(c)s END) - fecha_vencimiento)) FILTER (WHERE v), 0)::float AS atraso,
                      count(*) FILTER (WHERE fecha_vencimiento + %(g)s <= %(c)s
                                       AND (fecha_pago IS NULL OR fecha_pago > %(c)s)) AS impagas
               FROM (SELECT p.*, (fecha_vencimiento + %(g)s <= %(c)s
                                  AND fecha_vencimiento + %(g)s > %(c)s::date - 180) AS v
                     FROM pagos p WHERE fecha_emision <= %(c)s) x
               GROUP BY entidad_id""", {"c": c, "g": g}).fetchall(),
            columns=["entidad_id", "n_venc", "n_tarde", "atraso", "impagas"]).set_index("entidad_id").reindex(X.index)
        n_venc = pg["n_venc"].fillna(0).astype(float)
        X["pct_tarde_180d"] = np.where(n_venc > 0, pg["n_tarde"].fillna(0).astype(float) / n_venc.replace(0, 1), 0.0)
        X["atraso_prom_180d"] = pg["atraso"].fillna(0).astype(float)
        X["impagas_vencidas"] = pg["impagas"].fillna(0).astype(float)

    for a in spec["atributos"]:
        X[f"attr__{a}"] = [(at or {}).get(a) if isinstance((at or {}).get(a), str) else None for at in base["atributos"]]
    return X
