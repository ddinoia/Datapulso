"""Tubería única de ingesta: origen -> staging -> mapeo/validación -> tablas canónicas (upsert idempotente).

Garantías:
- Idempotente: re-cargar el mismo archivo/consulta no duplica (clave por tenant) y reporta 'sin cambios'.
- Atómica: la carga completa (staging, canónicas, marca de agua) se confirma junta o no se confirma.
- Auditable: las filas rechazadas quedan en stg_filas con su motivo; el reporte de calidad queda en `cargas`.
"""
import json
from collections import Counter
from datetime import datetime, timezone
from itertools import islice
from .mapeo import Mapeo, CAMPOS
from .validar import normalizar, clave_dedupe
from ..tenants import auditar

LOTE = 5000

_TMP = {
    "entidades": """CREATE TEMP TABLE _b (nro_fila int, id_externo text, tipo text, fecha_alta date, fecha_baja date,
                    estado_externo text, atributos jsonb) ON COMMIT DROP""",
    "eventos": """CREATE TEMP TABLE _b (nro_fila int, entidad_ext text, clave text, id_externo text, ts timestamptz,
                  tipo text, monto numeric, moneda text, canal text, categoria text, atributos jsonb) ON COMMIT DROP""",
    "pagos": """CREATE TEMP TABLE _b (nro_fila int, entidad_ext text, comprobante text, fecha_emision date,
                fecha_vencimiento date, monto numeric, moneda text, fecha_pago date, monto_pagado numeric,
                atributos jsonb) ON COMMIT DROP""",
}
_COLS = {
    "entidades": ["nro_fila", "id_externo", "tipo", "fecha_alta", "fecha_baja", "estado_externo", "atributos"],
    "eventos": ["nro_fila", "entidad_ext", "clave", "id_externo", "ts", "tipo", "monto", "moneda", "canal", "categoria", "atributos"],
    "pagos": ["nro_fila", "entidad_ext", "comprobante", "fecha_emision", "fecha_vencimiento", "monto", "moneda",
              "fecha_pago", "monto_pagado", "atributos"],
}

_UPSERT = {
    "entidades": """
WITH r AS (
  INSERT INTO entidades (tenant_id, id_externo, tipo, fecha_alta, fecha_baja, estado_externo, atributos, is_demo, carga_id)
  SELECT app_tenant(), b.id_externo, b.tipo, b.fecha_alta, b.fecha_baja, b.estado_externo, b.atributos, %(demo)s, %(carga)s FROM _b b
  ON CONFLICT (tenant_id, id_externo) DO UPDATE SET tipo=EXCLUDED.tipo, fecha_alta=EXCLUDED.fecha_alta,
    fecha_baja=EXCLUDED.fecha_baja, estado_externo=EXCLUDED.estado_externo, atributos=EXCLUDED.atributos,
    carga_id=EXCLUDED.carga_id, actualizado_en=now()
  WHERE (entidades.tipo, entidades.fecha_alta, entidades.fecha_baja, entidades.estado_externo, entidades.atributos)
        IS DISTINCT FROM (EXCLUDED.tipo, EXCLUDED.fecha_alta, EXCLUDED.fecha_baja, EXCLUDED.estado_externo, EXCLUDED.atributos)
  RETURNING (xmax = 0) AS ins)
SELECT count(*) FILTER (WHERE ins) AS ins, count(*) FILTER (WHERE NOT ins) AS upd FROM r""",
    "eventos": """
WITH r AS (
  INSERT INTO eventos (tenant_id, entidad_id, clave, id_externo, ts, tipo, monto, moneda, canal, categoria, atributos, is_demo, carga_id)
  SELECT app_tenant(), e.id, b.clave, b.id_externo, b.ts, b.tipo, b.monto, b.moneda, b.canal, b.categoria, b.atributos, %(demo)s, %(carga)s
  FROM _b b JOIN entidades e ON e.tenant_id = app_tenant() AND e.id_externo = b.entidad_ext
  ON CONFLICT (tenant_id, clave) DO UPDATE SET entidad_id=EXCLUDED.entidad_id, ts=EXCLUDED.ts, tipo=EXCLUDED.tipo,
    monto=EXCLUDED.monto, moneda=EXCLUDED.moneda, canal=EXCLUDED.canal, categoria=EXCLUDED.categoria,
    atributos=EXCLUDED.atributos, carga_id=EXCLUDED.carga_id
  WHERE (eventos.entidad_id, eventos.ts, eventos.tipo, eventos.monto, eventos.moneda, eventos.canal, eventos.categoria, eventos.atributos)
        IS DISTINCT FROM (EXCLUDED.entidad_id, EXCLUDED.ts, EXCLUDED.tipo, EXCLUDED.monto, EXCLUDED.moneda, EXCLUDED.canal, EXCLUDED.categoria, EXCLUDED.atributos)
  RETURNING (xmax = 0) AS ins)
SELECT count(*) FILTER (WHERE ins) AS ins, count(*) FILTER (WHERE NOT ins) AS upd FROM r""",
    "pagos": """
WITH r AS (
  INSERT INTO pagos (tenant_id, entidad_id, comprobante, fecha_emision, fecha_vencimiento, monto, moneda, fecha_pago, monto_pagado, atributos, is_demo, carga_id)
  SELECT app_tenant(), e.id, b.comprobante, b.fecha_emision, b.fecha_vencimiento, b.monto, b.moneda, b.fecha_pago, b.monto_pagado, b.atributos, %(demo)s, %(carga)s
  FROM _b b JOIN entidades e ON e.tenant_id = app_tenant() AND e.id_externo = b.entidad_ext
  ON CONFLICT (tenant_id, comprobante) DO UPDATE SET entidad_id=EXCLUDED.entidad_id, fecha_emision=EXCLUDED.fecha_emision,
    fecha_vencimiento=EXCLUDED.fecha_vencimiento, monto=EXCLUDED.monto, moneda=EXCLUDED.moneda,
    fecha_pago=EXCLUDED.fecha_pago, monto_pagado=EXCLUDED.monto_pagado, atributos=EXCLUDED.atributos, carga_id=EXCLUDED.carga_id
  WHERE (pagos.entidad_id, pagos.fecha_emision, pagos.fecha_vencimiento, pagos.monto, pagos.moneda, pagos.fecha_pago, pagos.monto_pagado, pagos.atributos)
        IS DISTINCT FROM (EXCLUDED.entidad_id, EXCLUDED.fecha_emision, EXCLUDED.fecha_vencimiento, EXCLUDED.monto, EXCLUDED.moneda, EXCLUDED.fecha_pago, EXCLUDED.monto_pagado, EXCLUDED.atributos)
  RETURNING (xmax = 0) AS ins)
SELECT count(*) FILTER (WHERE ins) AS ins, count(*) FILTER (WHERE NOT ins) AS upd FROM r""",
}

_FECHAS_RANGO = {"entidades": "fecha_alta", "eventos": "ts", "pagos": "fecha_emision"}


def _json(o):
    return json.dumps(o, default=str, ensure_ascii=False)


def _lotes(it, n):
    while True:
        lote = list(islice(it, n))
        if not lote:
            return
        yield lote


def ejecutar_carga(conn, fuente: dict, origen) -> dict:
    """Corre una carga completa de `fuente` (fila de la tabla fuentes) desde `origen`. Devuelve el resumen."""
    mapeo = Mapeo.model_validate(fuente["mapeo"])
    destino = mapeo.destino
    tz = conn.execute("SELECT zona_horaria, is_demo FROM tenants").fetchone()
    demo = bool(fuente["is_demo"])
    marca_antes = fuente.get("marca_de_agua")
    carga_id = conn.execute(
        """INSERT INTO cargas (tenant_id, fuente_id, origen, marca_antes, is_demo)
           VALUES (app_tenant(), %s, %s, %s, %s) RETURNING id""",
        (fuente["id"], origen.descripcion, marca_antes, demo)).fetchone()["id"]
    conn.commit()  # la carga queda registrada aunque falle lo que sigue

    try:
        res = _procesar(conn, fuente, mapeo, destino, tz["zona_horaria"], demo, carga_id, origen, marca_antes)
        conn.commit()
        return res
    except Exception as e:
        conn.rollback()
        conn.execute("UPDATE cargas SET estado='error', fin=now(), error=%s WHERE id=%s", (str(e)[:2000], carga_id))
        conn.commit()
        raise


def _procesar(conn, fuente, mapeo, destino, tz, demo, carga_id, origen, marca_antes) -> dict:
    ahora = datetime.now(timezone.utc)
    conn.execute(_TMP[destino])
    cols = _COLS[destino]
    leidas = aceptadas = ins = upd = 0
    motivos: Counter = Counter()
    ejemplos: list[dict] = []
    vistos: set[str] = set()
    padres: dict[str, str] = {}
    rango = {"min": None, "max": None}
    nulos: Counter = Counter()
    n_ok = 0
    nro = 0

    def rechazar(buenas_nro: int, motivo: str, rechazos: list):
        motivos[motivo] += 1
        rechazos.append((motivo, carga_id, buenas_nro))
        if len(ejemplos) < 8:
            ejemplos.append({"nro_fila": buenas_nro, "motivo": motivo})

    for lote in _lotes(iter(origen.filas(marca_antes)), LOTE):
        staging, buenas, rechazos = [], [], []
        for crudo in lote:
            nro += 1
            staging.append((carga_id, nro, _json(crudo)))
        leidas += len(lote)
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO stg_filas (tenant_id, carga_id, nro_fila, crudo) VALUES (app_tenant(), %s,%s,%s::jsonb)", staging)
        for (_, n, js) in staging:
            fila, motivo = normalizar(mapeo, json.loads(js), tz, ahora)
            if motivo:
                rechazar(n, motivo, rechazos)
                continue
            k = clave_dedupe(destino, fila)
            if k in vistos:
                rechazar(n, "duplicado_en_carga", rechazos)
                continue
            vistos.add(k)
            buenas.append((n, fila))

        # --- a tabla temporal del lote
        conn.execute("TRUNCATE _b")
        with conn.cursor() as cur, cur.copy(f"COPY _b ({', '.join(cols)}) FROM STDIN") as cp:
            for n, f in buenas:
                f2 = dict(f, nro_fila=n, atributos=_json(f["atributos"]))
                if destino != "entidades":
                    f2["entidad_ext"] = f["entidad_id_externo"]
                cp.write_row([f2.get(c) for c in cols])

        # --- eventos/pagos de entidades desconocidas -> rechazo
        if destino != "entidades":
            desconocidas = conn.execute(
                """SELECT b.nro_fila FROM _b b LEFT JOIN entidades e
                   ON e.tenant_id = app_tenant() AND e.id_externo = b.entidad_ext WHERE e.id IS NULL""").fetchall()
            if desconocidas:
                ids = [r["nro_fila"] for r in desconocidas]
                conn.execute("DELETE FROM _b WHERE nro_fila = ANY(%s)", (ids,))
                for n in ids:
                    rechazar(n, "entidad_desconocida", rechazos)
                excl = set(ids)
                buenas = [(n, f) for (n, f) in buenas if n not in excl]

        r = conn.execute(_UPSERT[destino], {"demo": demo, "carga": carga_id}).fetchone()
        ins += r["ins"]
        upd += r["upd"]
        aceptadas += len(buenas)

        # --- seguimiento de rangos, nulos y padres
        campo_f = _FECHAS_RANGO[destino]
        for n, f in buenas:
            v = f[campo_f]
            rango["min"] = v if rango["min"] is None or v < rango["min"] else rango["min"]
            rango["max"] = v if rango["max"] is None or v > rango["max"] else rango["max"]
            if destino == "entidades" and f.get("parent_id_externo"):
                padres[f["id_externo"]] = f["parent_id_externo"]
            n_ok += 1
            for c in CAMPOS[destino]["opcionales"]:
                if f.get(c) is None:
                    nulos[c] += 1

        # --- cierre de staging del lote: aceptadas se borran, rechazadas quedan con motivo
        if rechazos:
            with conn.cursor() as cur:
                cur.executemany("UPDATE stg_filas SET estado='rechazada', motivo=%s WHERE carga_id=%s AND nro_fila=%s", rechazos)
        conn.execute("DELETE FROM stg_filas WHERE carga_id=%s AND estado='pendiente'", (carga_id,))

    advertencias = []
    if destino == "entidades" and padres:
        sin_padre = conn.execute(
            """WITH m AS (SELECT * FROM unnest(%s::text[], %s::text[]) AS m(hijo, padre)),
                    u AS (UPDATE entidades e SET parent_id = p.id FROM m
                          JOIN entidades p ON p.tenant_id = app_tenant() AND p.id_externo = m.padre
                          WHERE e.tenant_id = app_tenant() AND e.id_externo = m.hijo AND m.hijo <> m.padre
                            AND e.parent_id IS DISTINCT FROM p.id RETURNING 1)
               SELECT (SELECT count(*) FROM m LEFT JOIN entidades p ON p.tenant_id = app_tenant() AND p.id_externo = m.padre
                       WHERE p.id IS NULL) AS sin_padre""",
            (list(padres.keys()), list(padres.values()))).fetchone()["sin_padre"]
        if sin_padre:
            advertencias.append(f"{sin_padre} filas referencian una empresa (parent_id_externo) que no existe; quedaron sin empresa.")

    rechazadas = leidas - aceptadas
    sin_cambios = aceptadas - ins - upd
    marca_despues = getattr(origen, "marca_nueva", None)
    reporte = {
        "destino": destino,
        "filas": {"leidas": leidas, "aceptadas": aceptadas, "rechazadas": rechazadas,
                  "insertadas": ins, "actualizadas": upd, "sin_cambios": sin_cambios},
        "rechazos_por_motivo": dict(motivos),
        "ejemplos_rechazo": ejemplos,
        "rango": {"campo": _FECHAS_RANGO[destino], "desde": str(rango["min"]) if rango["min"] else None,
                  "hasta": str(rango["max"]) if rango["max"] else None},
        "nulos_opcionales_pct": {c: round(100 * v / n_ok, 1) for c, v in nulos.items()} if n_ok else {},
        "advertencias": advertencias,
        "marca_de_agua": {"antes": marca_antes, "despues": marca_despues},
    }
    if leidas and rechazadas / leidas > 0.2:
        reporte["advertencias"].append(f"Más del 20% de filas rechazadas ({rechazadas}/{leidas}): revisar el mapeo o la fuente.")

    conn.execute("""UPDATE cargas SET estado='ok', fin=now(), filas_leidas=%s, filas_aceptadas=%s, filas_rechazadas=%s,
                    filas_insertadas=%s, filas_actualizadas=%s, marca_despues=%s, reporte_calidad=%s::jsonb WHERE id=%s""",
                 (leidas, aceptadas, rechazadas, ins, upd, marca_despues, _json(reporte), carga_id))
    if marca_despues is not None and marca_despues != marca_antes:
        conn.execute("UPDATE fuentes SET marca_de_agua=%s WHERE id=%s", (marca_despues, fuente["id"]))
    auditar(conn, "sistema", "carga_ejecutada", {"carga_id": str(carga_id), "fuente": fuente["nombre"],
                                                  **reporte["filas"]})
    reporte["carga_id"] = str(carga_id)
    return reporte
