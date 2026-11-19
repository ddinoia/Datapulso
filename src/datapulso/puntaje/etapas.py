"""Qué predice el puntaje en cada etapa: población puntuable a una fecha y evento modelado en el horizonte.

El puntaje siempre es "la probabilidad del evento modelado", y el evento se eligió para que puntaje alto
= prioridad alta (decisión pendiente de confirmar con Diego, ver la guía del hito 2):

  retencion   población: vigentes a c.             evento: se da de baja en (c, c+H].
              Si el tenant no informa fechas de baja, evento = queda inactivo según la regla de retención a c+H.
  cobranza    población: vigentes con algún comprobante emitido hasta c.
              evento: paga fuera de término (o no paga) algún comprobante que vence en (c, c+H-gracia].
              Sin comprobantes en esa ventana no hay etiqueta (la fila no entra al entrenamiento).
  activacion  población: altas de los últimos `dias` que todavía no cumplen la regla de activación a c.
              evento: NO cumple la regla dentro de los `dias` desde el alta (H = dias de la regla).
  uso_valor   población: vigentes con al menos `dias` de antigüedad que hoy no cumplen la regla de uso y valor.
              evento: cumple la regla en (c, c+H] (H = dias de la regla). Es una oportunidad, no un riesgo.

La etapa "alta" no se puntúa: los datos no traen los pasos del alta.
"""
from __future__ import annotations
from datetime import date, timedelta
import numpy as np
import pandas as pd
from .variables import base_vigentes, inicio_dia, fin_dia, zona

PUNTUABLES = ("retencion", "cobranza", "activacion", "uso_valor")


def horizonte(etapa: str, cfg) -> int:
    e = cfg.etapas[etapa]
    if etapa in ("activacion", "uso_valor"):
        return e.ventana_prediccion_dias or e.evento_objetivo.dias
    return e.ventana_prediccion_dias or e.ventana_medicion_dias


def puntuable(etapa: str, cfg, spec: dict) -> str | None:
    """None si la etapa se puede puntuar; si no, el motivo."""
    e = cfg.etapas.get(etapa)
    if e is None or not e.activa:
        return "etapa inactiva en la configuración"
    if etapa == "cobranza" and not spec["pagos"]:
        return "no hay comprobantes cargados"
    if etapa in ("activacion", "uso_valor", "retencion") and e.evento_objetivo is None:
        return "la etapa no tiene evento objetivo configurado"
    if not spec["tipos"]:
        return "no hay eventos cargados"
    return None


def evento_modelado(etapa: str, spec: dict) -> str:
    """Clave de texto (textos/es.json: evento_modelado.*)."""
    if etapa == "retencion" and not spec["hay_bajas"]:
        return "retencion_inactividad"
    return etapa


def _contar(conn, tipos: list[str], ids, desde, hasta) -> pd.Series:
    """Eventos de `tipos` (vacío = todos) por entidad en [desde_i, hasta_i) (timestamptz)."""
    ids = list(ids)
    if not ids:
        return pd.Series(dtype=float)
    filtro = "AND ev.tipo = ANY(%(tipos)s)" if tipos else ""
    rows = conn.execute(
        f"""SELECT v.id AS entidad_id, count(ev.id) AS n
            FROM unnest(%(ids)s::uuid[], %(d)s::timestamptz[], %(h)s::timestamptz[]) AS v(id, d, h)
            LEFT JOIN eventos ev ON ev.tenant_id = app_tenant() AND ev.entidad_id = v.id
                 AND ev.ts >= v.d AND ev.ts < v.h {filtro}
            GROUP BY v.id""", {"ids": ids, "d": list(desde), "h": list(hasta), "tipos": tipos}).fetchall()
    return pd.Series({r["entidad_id"]: float(r["n"]) for r in rows}).reindex(ids).fillna(0.0)


def poblacion(conn, etapa: str, cfg, c: date) -> pd.DataFrame:
    """Base de entidades puntuables a la fecha c (índice entidad_id)."""
    base = base_vigentes(conn, c)
    if base.empty:
        return base
    tz = zona(conn)
    if etapa == "retencion":
        return base
    if etapa == "cobranza":
        con = {r["entidad_id"] for r in conn.execute(
            "SELECT DISTINCT entidad_id FROM pagos WHERE fecha_emision <= %s", (c,)).fetchall()}
        return base[base.index.isin(con)]
    r = cfg.etapas[etapa].evento_objetivo
    if etapa == "activacion":
        b = base[[(c - d).days < r.dias for d in base["fecha_alta"]]]
        n = _contar(conn, r.eventos_tipo, b.index, [inicio_dia(d, tz) for d in b["fecha_alta"]], [fin_dia(c, tz)] * len(b))
        return b[(n < r.min_eventos).values]
    if etapa == "uso_valor":
        b = base[[(c - d).days >= r.dias for d in base["fecha_alta"]]]
        n = _contar(conn, r.eventos_tipo, b.index, [inicio_dia(c - timedelta(days=r.dias - 1), tz)] * len(b),
                    [fin_dia(c, tz)] * len(b))
        return b[(n < r.min_eventos).values]
    raise ValueError(f"etapa no puntuable: {etapa}")


def etiqueta(conn, etapa: str, cfg, spec: dict, pob: pd.DataFrame, c: date, h: int) -> pd.Series:
    """1/0 si ocurrió el evento modelado en el horizonte; NaN si no aplica. Solo para cortes con c + h <= hoy."""
    ids = pob.index
    if len(ids) == 0:
        return pd.Series(dtype=float)
    tz = zona(conn)
    fin = c + timedelta(days=h)
    if etapa == "retencion":
        if spec["hay_bajas"]:
            rows = conn.execute("""SELECT id FROM entidades WHERE id = ANY(%s) AND fecha_baja > %s AND fecha_baja <= %s""",
                                (list(ids), c, fin)).fetchall()
            bajas = {r["id"] for r in rows}
            return pd.Series([1.0 if i in bajas else 0.0 for i in ids], index=ids)
        r = cfg.etapas["retencion"].evento_objetivo
        n = _contar(conn, r.eventos_tipo, ids, [inicio_dia(fin - timedelta(days=r.dias - 1), tz)] * len(ids),
                    [fin_dia(fin, tz)] * len(ids))
        return pd.Series((n.values < r.min_eventos).astype(float), index=ids)
    if etapa == "cobranza":
        g = spec["gracia"]
        rows = conn.execute(
            """SELECT entidad_id, bool_or(fecha_pago IS NULL OR fecha_pago > fecha_vencimiento + %(g)s) AS tarde
               FROM pagos WHERE entidad_id = ANY(%(ids)s) AND fecha_vencimiento > %(c)s
                 AND fecha_vencimiento + %(g)s <= %(fin)s
               GROUP BY entidad_id""", {"g": g, "ids": list(ids), "c": c, "fin": fin}).fetchall()
        m = {r["entidad_id"]: float(r["tarde"]) for r in rows}
        return pd.Series([m.get(i, np.nan) for i in ids], index=ids)
    r = cfg.etapas[etapa].evento_objetivo
    if etapa == "activacion":
        n = _contar(conn, r.eventos_tipo, ids, [inicio_dia(d, tz) for d in pob["fecha_alta"]],
                    [inicio_dia(d + timedelta(days=r.dias), tz) for d in pob["fecha_alta"]])
        return pd.Series((n.values < r.min_eventos).astype(float), index=ids)
    if etapa == "uso_valor":
        n = _contar(conn, r.eventos_tipo, ids, [fin_dia(c, tz)] * len(ids), [fin_dia(fin, tz)] * len(ids))
        return pd.Series((n.values >= r.min_eventos).astype(float), index=ids)
    raise ValueError(f"etapa no puntuable: {etapa}")
