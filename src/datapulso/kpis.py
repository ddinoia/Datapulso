"""KPIs y funnel calculados desde la base, con el vocabulario y las reglas del tenant.

Cada etapa se evalúa con la regla de `config.etapas[etapa].evento_objetivo`:
- desde_alta: alcanzaron la etapa los clientes con >= min_eventos en los primeros `dias` desde el alta.
  Solo son "elegibles" los clientes cuya ventana ya terminó (alta + dias <= corte).
- ventana_reciente: alcanzaron la etapa los clientes con >= min_eventos en los últimos `dias` antes del corte.
  Son elegibles los clientes dados de alta antes del inicio de esa ventana y no dados de baja antes.
La cobranza se mide sobre los comprobantes vencidos al corte.
"""
from datetime import date, datetime, timedelta
from .tenants import config_activa, tenant_info
from .config import ETAPAS


def _tz(conn) -> str:
    return conn.execute("SELECT zona_horaria FROM tenants").fetchone()["zona_horaria"]


def fecha_corte_por_defecto(conn) -> date:
    r = conn.execute("""SELECT GREATEST(
                          (SELECT (max(ts) AT TIME ZONE %(tz)s)::date FROM eventos),
                          (SELECT max(fecha_emision) FROM pagos),
                          (SELECT max(fecha_alta) FROM entidades)) AS d""", {"tz": _tz(conn)}).fetchone()["d"]
    if r is None:
        raise ValueError("No hay datos cargados")
    return r


def _etapa_regla(conn, regla, corte: date) -> dict:
    tipos = regla.eventos_tipo
    filtro_tipo = "AND ev.tipo = ANY(%(tipos)s)" if tipos else ""
    p = {"tipos": tipos, "min": regla.min_eventos, "dias": regla.dias, "corte": corte, "tz": _tz(conn)}
    if regla.modo == "desde_alta":
        q = f"""
        SELECT count(*) AS elegibles,
               count(*) FILTER (WHERE (SELECT count(*) FROM eventos ev
                  WHERE ev.tenant_id = e.tenant_id AND ev.entidad_id = e.id {filtro_tipo}
                    AND (ev.ts AT TIME ZONE %(tz)s)::date >= e.fecha_alta AND (ev.ts AT TIME ZONE %(tz)s)::date < e.fecha_alta + %(dias)s) >= %(min)s) AS alcanzaron
        FROM entidades e
        WHERE e.tipo = 'persona' AND e.fecha_alta + %(dias)s <= %(corte)s"""
    else:
        q = f"""
        SELECT count(*) AS elegibles,
               count(*) FILTER (WHERE (SELECT count(*) FROM eventos ev
                  WHERE ev.tenant_id = e.tenant_id AND ev.entidad_id = e.id {filtro_tipo}
                    AND (ev.ts AT TIME ZONE %(tz)s)::date > %(corte)s - %(dias)s AND (ev.ts AT TIME ZONE %(tz)s)::date <= %(corte)s) >= %(min)s) AS alcanzaron
        FROM entidades e
        WHERE e.tipo = 'persona' AND e.fecha_alta <= %(corte)s - %(dias)s
          AND (e.fecha_baja IS NULL OR e.fecha_baja > %(corte)s - %(dias)s)"""
    r = conn.execute(q, p).fetchone()
    return {"elegibles": r["elegibles"], "alcanzaron": r["alcanzaron"],
            "tasa": round(r["alcanzaron"] / r["elegibles"], 4) if r["elegibles"] else None}


def calcular(conn, corte: date | None = None) -> dict:
    version, cfg = config_activa(conn)
    t = tenant_info(conn)
    corte = corte or fecha_corte_por_defecto(conn)
    voc = cfg.vocabulario
    out: dict = {
        "tenant": t["nombre"], "industria": t["industria"], "config_version": version,
        "fecha_corte": str(corte), "moneda": t["moneda"],
        "es_demo": t["is_demo"],
    }

    tot = conn.execute("""SELECT count(*) FILTER (WHERE tipo='persona') AS personas,
                                 count(*) FILTER (WHERE tipo='empresa') AS empresas,
                                 count(*) FILTER (WHERE tipo='persona' AND fecha_baja IS NULL) AS vigentes,
                                 count(*) FILTER (WHERE tipo='persona' AND fecha_baja IS NOT NULL AND fecha_baja <= %s) AS bajas
                          FROM entidades WHERE fecha_alta <= %s""", (corte, corte)).fetchone()
    out["resumen"] = {
        f"{voc.clientes} (total)": tot["personas"],
        f"{voc.clientes} vigentes": tot["vigentes"],
        f"{voc.clientes} dados de baja": tot["bajas"],
        "Empresas": tot["empresas"],
    }

    altas = conn.execute("""SELECT to_char(date_trunc('month', fecha_alta), 'YYYY-MM') AS mes, count(*) AS n
                            FROM entidades WHERE tipo='persona' AND fecha_alta <= %s AND fecha_alta > %s::date - 365
                            GROUP BY 1 ORDER BY 1""", (corte, corte)).fetchall()
    out["altas_por_mes"] = {r["mes"]: r["n"] for r in altas}

    funnel = []
    for k in ETAPAS:
        e = cfg.etapas.get(k)
        if not e or not e.activa:
            continue
        etiqueta = voc.etapas.get(k, k)
        if k == "alta":
            funnel.append({"etapa": k, "etiqueta": etiqueta, "elegibles": tot["personas"],
                           "alcanzaron": tot["personas"], "tasa": 1.0 if tot["personas"] else None,
                           "regla": "Todos los clientes dados de alta"})
        elif k == "cobranza":
            funnel.append({"etapa": k, "etiqueta": etiqueta, **_cobranza(conn, corte, cfg.cobranza.gracia_dias)})
        else:
            r = _etapa_regla(conn, e.evento_objetivo, corte)
            rg = e.evento_objetivo
            r["regla"] = (f"≥{rg.min_eventos} eventos"
                          + (f" de tipo {', '.join(rg.eventos_tipo)}" if rg.eventos_tipo else "")
                          + (f" en los primeros {rg.dias} días desde el alta" if rg.modo == "desde_alta"
                             else f" en los últimos {rg.dias} días"))
            funnel.append({"etapa": k, "etiqueta": etiqueta, **r})
    out["funnel"] = funnel

    if cfg.segmento_atributo:
        seg = conn.execute(
            """SELECT COALESCE(atributos->>%s, '(sin dato)') AS segmento, count(*) AS n,
                      count(*) FILTER (WHERE fecha_baja IS NOT NULL AND fecha_baja <= %s) AS bajas
               FROM entidades WHERE tipo='persona' AND fecha_alta <= %s GROUP BY 1 ORDER BY 2 DESC""",
            (cfg.segmento_atributo, corte, corte)).fetchall()
        out["segmentos"] = {"atributo": cfg.segmento_atributo,
                            "filas": [{"segmento": r["segmento"], "clientes": r["n"], "bajas": r["bajas"]} for r in seg]}
    return out


def _cobranza(conn, corte: date, gracia: int) -> dict:
    r = conn.execute(
        """SELECT count(*) AS elegibles,
                  count(*) FILTER (WHERE fecha_pago IS NOT NULL AND fecha_pago <= fecha_vencimiento + %(g)s
                                   AND fecha_pago <= %(c)s) AS alcanzaron,
                  COALESCE(sum(monto - COALESCE(monto_pagado,0)) FILTER (WHERE (fecha_pago IS NULL OR fecha_pago > %(c)s)), 0) AS monto_impago
           FROM pagos WHERE fecha_vencimiento <= %(c)s""", {"g": gracia, "c": corte}).fetchone()
    return {"elegibles": r["elegibles"], "alcanzaron": r["alcanzaron"],
            "tasa": round(r["alcanzaron"] / r["elegibles"], 4) if r["elegibles"] else None,
            "monto_vencido_impago": float(r["monto_impago"]),
            "regla": f"Comprobantes pagados hasta {gracia} días después del vencimiento"}


def calidad_datos(conn) -> dict:
    """Cobertura y consistencia de lo ya cargado (complementa el reporte por carga)."""
    r = conn.execute("""SELECT
        (SELECT count(*) FROM entidades) AS entidades,
        (SELECT count(*) FROM eventos) AS eventos,
        (SELECT count(*) FROM pagos) AS pagos,
        (SELECT count(*) FROM entidades e WHERE tipo='persona' AND EXISTS (SELECT 1 FROM eventos v WHERE v.tenant_id=e.tenant_id AND v.entidad_id=e.id)) AS con_eventos,
        (SELECT count(*) FROM entidades WHERE tipo='persona') AS personas,
        (SELECT count(*) FROM entidades WHERE tipo='persona' AND parent_id IS NOT NULL) AS con_empresa,
        (SELECT (min(ts) AT TIME ZONE %(tz)s)::date FROM eventos) AS ev_desde, (SELECT (max(ts) AT TIME ZONE %(tz)s)::date FROM eventos) AS ev_hasta""", {"tz": _tz(conn)}).fetchone()
    return {
        "entidades": r["entidades"], "eventos": r["eventos"], "pagos": r["pagos"],
        "cobertura_clientes_con_eventos_pct": round(100 * r["con_eventos"] / r["personas"], 1) if r["personas"] else None,
        "clientes_con_empresa": r["con_empresa"],
        "eventos_desde": str(r["ev_desde"]) if r["ev_desde"] else None,
        "eventos_hasta": str(r["ev_hasta"]) if r["ev_hasta"] else None,
    }
