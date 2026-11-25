"""Corridas: crear (borrador), asignar tratamiento y control, proyectar, pedir aprobación, aprobar (congela la
proyección), cancelar con motivo y marcar lanzada.

Garantías que no dependen solo de este código (ver db/migrations/003_acciones.sql): transiciones de estado
válidas, lista y grupos escritos solo en borrador, proyección y diseño inmutables desde la aprobación, un
cliente en una sola corrida abierta por etapa, y las corridas no se borran.
"""
from __future__ import annotations
import json
import math
import secrets
from datetime import date, datetime, timezone
import numpy as np
import pandas as pd
from ..tenants import config_activa, auditar
from ..textos import t
from . import ErrorAcciones, catalogo, politica, usuarios
from .base import puntajes_etapa
from .recomendacion import elegible

Z_ALFA, Z_BETA = 1.959964, 0.841621    # 95 % de confianza a dos colas, 80 % de potencia


# ---------------------------------------------------------------- asignación (D5)
def asignar_grupos(ids: list, estratos: list, pct_control: float, semilla: int) -> np.ndarray:
    """Asignación aleatoria estratificada y reproducible.

    El total de control es exactamente round(N · pct). Cada estrato recibe floor(n_s · pct) y los restantes van a
    los estratos con mayor resto (desempate por nombre del estrato): así cada estrato se aparta de su cuota exacta
    en menos de 1 cliente, que es la tolerancia de balance que se prueba. Dentro de cada estrato, quiénes van a
    control sale de una permutación con la semilla registrada, sobre los clientes ordenados por id.
    """
    df = pd.DataFrame({"id": [str(i) for i in ids], "e": estratos}).reset_index()
    frac = pct_control / 100.0
    total = int(round(len(df) * frac))
    tam = df.groupby("e").size().sort_index()
    cuota = tam * frac
    base = np.floor(cuota).astype(int)
    resto = (cuota - base).sort_values(ascending=False, kind="stable")
    extra = total - int(base.sum())
    for e in list(resto.index)[:max(extra, 0)]:
        base[e] += 1
    rng = np.random.default_rng(semilla)
    grupo = np.array(["tratamiento"] * len(df), dtype=object)
    for e in tam.index:
        miembros = df[df["e"] == e].sort_values("id")
        orden = rng.permutation(len(miembros))
        control = miembros.iloc[orden[: base[e]]]["index"].values
        grupo[control] = "control"
    return grupo


def efecto_minimo_detectable(p: float, n_t: int, n_c: int) -> float:
    """Diferencia absoluta mínima entre tasas que se detectaría con 95 % de confianza y 80 % de potencia (aprox.)."""
    if n_t < 1 or n_c < 1:
        return float("inf")
    return (Z_ALFA + Z_BETA) * math.sqrt(max(p * (1 - p), 1e-9) * (1 / n_t + 1 / n_c))


# ---------------------------------------------------------------- proyección (D2)
def proyectar(miembros: pd.DataFrame, accion: dict, cfg, version_cfg: int, meta: dict, calidad_modelo: dict | None) -> dict:
    """Proyección de una corrida a partir de las probabilidades calibradas de sus clientes.

    `miembros`: columnas grupo, banda, puntaje. Impacto = reducción relativa de la tasa del evento adverso (D1).
    Rango del efecto: impacto × (1 ± rango_impacto) porque el impacto es un supuesto, no una medición.
    """
    i = float(accion["impacto_esperado"])
    r = cfg.acciones.rango_impacto
    i_min, i_max = i * (1 - r), min(1.0, i * (1 + r))
    valor = cfg.valor_economico.valor_por_resultado.get(accion["etapa"])
    trat = miembros[miembros["grupo"] == "tratamiento"]
    ctrl = miembros[miembros["grupo"] == "control"]
    n_t, n_c = len(trat), len(ctrl)
    tasa_t = float(trat["puntaje"].mean())
    sin_accion_t = float(trat["puntaje"].sum())                  # eventos esperados sin acción en tratamiento
    costo = float(accion["costo_unitario"]) * n_t

    def escenario(imp: float) -> dict:
        evit = sin_accion_t * imp
        val = evit * valor if valor is not None else None
        roi = (val - costo) / costo if (val is not None and costo > 0) else None
        return {"efecto_esperado": round(tasa_t * imp, 6), "tasa_esperada_con_accion": round(tasa_t * (1 - imp), 6),
                "eventos_evitados": round(evit, 3), "valor_esperado": None if val is None else round(val, 2),
                "roi_esperado": None if roi is None else round(roi, 4)}

    por_banda = []
    for b in ("alta", "media", "baja"):
        m = miembros[miembros["banda"] == b]
        if len(m):
            mt = m[m["grupo"] == "tratamiento"]
            por_banda.append({"banda": b, "clientes": int(len(m)), "tratamiento": int(len(mt)),
                              "control": int(len(m) - len(mt)),
                              "tasa_sin_accion": round(float(m["puntaje"].mean()), 6),
                              "eventos_evitados": round(float(mt["puntaje"].sum()) * i, 3)})
    return {
        "etapa": accion["etapa"],
        "accion": {"codigo": accion["codigo"], "version": accion["version"], "nombre": accion["nombre"],
                   "impacto_esperado": i, "impacto_origen": accion["impacto_origen"],
                   "costo_unitario": float(accion["costo_unitario"])},
        "tamanos": {"tratamiento": n_t, "control": n_c, "total": n_t + n_c},
        "tasa_esperada_sin_accion": {"total": round(float(miembros["puntaje"].mean()), 6),
                                     "tratamiento": round(tasa_t, 6), "control": round(float(ctrl["puntaje"].mean()), 6)},
        "eventos_esperados_sin_accion_tratamiento": round(sin_accion_t, 3),
        "por_banda": por_banda,
        "esperado": escenario(i),
        "rango": {"impacto": [round(i_min, 4), round(i_max, 4)], "bajo": escenario(i_min), "alto": escenario(i_max)},
        "valor_por_resultado": valor, "costo_total": round(costo, 2),
        "versiones": {"modelo": meta["modelo_version"], "config": version_cfg, "fecha_puntajes": str(meta["fecha"])},
        "calibracion_modelo": calidad_modelo,
    }


def _calibracion_modelo(conn, version: str) -> dict | None:
    m = conn.execute("SELECT calidad FROM modelos WHERE version = %s", (version,)).fetchone()
    c = (((m or {}).get("calidad") or {}).get("backtest") or {}).get("modelo", {}).get("calibracion")
    return None if c is None else {"cumple": c["cumple"], "evaluable": c["evaluable"], "error_max": c["error_max"]}


# ---------------------------------------------------------------- consultas
def obtener(conn, ref) -> dict:
    """Por número (C-3, 3) o por id."""
    s = str(ref).strip().upper().removeprefix("C-")
    if s.isdigit():
        c = conn.execute("SELECT * FROM corridas WHERE numero = %s", (int(s),)).fetchone()
    else:
        c = conn.execute("SELECT * FROM corridas WHERE id::text = %s", (str(ref),)).fetchone()
    if not c:
        raise ErrorAcciones(f"No existe la corrida {ref}")
    return c


def listar(conn, estado: str | None = None) -> list[dict]:
    return conn.execute(
        """SELECT c.*, a.codigo, a.nombre AS accion_nombre, u.nombre AS creada_por_nombre, ap.nombre AS aprobada_por_nombre
           FROM corridas c JOIN acciones_catalogo a ON a.id = c.accion_id JOIN usuarios u ON u.id = c.creada_por
           LEFT JOIN usuarios ap ON ap.id = c.aprobada_por
           WHERE (%(e)s::text IS NULL OR c.estado = %(e)s) ORDER BY c.numero""", {"e": estado}).fetchall()


def miembros(conn, corrida_id) -> pd.DataFrame:
    """Clientes de la corrida. `empresa` y `motivo` vienen como texto vacío cuando no hay (nunca NaN: el CSV y el
    JSON del webhook no deben llevar "nan")."""
    df = pd.DataFrame(conn.execute(
        """SELECT ce.*, e.id_externo, COALESCE(emp.id_externo, '') AS empresa FROM corrida_entidades ce
           JOIN entidades e ON e.id = ce.entidad_id LEFT JOIN entidades emp ON emp.id = e.parent_id
           WHERE ce.corrida_id = %s ORDER BY ce.grupo DESC, e.id_externo""", (corrida_id,)).fetchall())
    if not df.empty:
        df["motivo"] = df["motivo"].fillna("")
    return df


def _exigir_estado(c: dict, estados: tuple, operacion: str) -> None:
    if c["estado"] not in estados:
        raise ErrorAcciones(t("acciones.error.estado", numero=c["numero"], estado=t("acciones.estado." + c["estado"]),
                              operacion=operacion, esperado=" o ".join(t("acciones.estado." + e) for e in estados)))


def _edad_dias(calculado_en) -> int:
    if isinstance(calculado_en, str):
        calculado_en = datetime.fromisoformat(calculado_en)
    return (datetime.now(timezone.utc) - calculado_en).days


# ---------------------------------------------------------------- crear
def crear(conn, codigo_accion: str, usuario: str, *, bandas: list[str] | None = None, segmentos: list[str] | None = None,
          limite: int | None = None, semilla: int | None = None, pct_control: float | None = None,
          hoy: date | None = None) -> dict:
    """Crea la corrida en borrador con su lista, sus grupos, el diseño y una proyección preliminar."""
    u = usuarios.exigir(conn, usuario, "crear_corrida")
    version_cfg, cfg = config_activa(conn)
    accion = catalogo.vigente(conn, codigo_accion)
    etapa = accion["etapa"]
    df, meta = puntajes_etapa(conn, etapa, cfg)
    edad = _edad_dias(meta["calculado_en"])
    if edad > cfg.acciones.antiguedad_max_puntajes_dias:
        raise ErrorAcciones(t("acciones.error.puntajes_viejos", dias=edad, max=cfg.acciones.antiguedad_max_puntajes_dias))

    # elegibilidad de la acción y filtros de quien crea la corrida
    n_puntuados = len(df)
    df = df[[elegible(accion, b, s) for b, s in zip(df["banda"], df["segmento"])]]
    if bandas:
        df = df[df["banda"].isin(bandas)]
    if segmentos:
        df = df[df["segmento"].isin(segmentos)]
    n_elegibles = len(df)
    aceptados, excluidos = politica.aplicar(conn, cfg, accion, df, hoy)
    if limite:
        aceptados = aceptados.sort_values(["puntaje", "id_externo"], ascending=[False, True]).head(limite)
    if aceptados.empty:
        raise ErrorAcciones(t("acciones.error.sin_clientes"))

    pct = float(pct_control if pct_control is not None else
                (cfg.control_pct_cobranza if etapa == "cobranza" else cfg.control_pct))
    semilla = int(semilla if semilla is not None else secrets.randbelow(2**31 - 1))
    aceptados = aceptados.sort_values("id_externo").reset_index(drop=True)
    estratos = (aceptados["banda"] + " · " + aceptados["segmento"]).tolist()
    aceptados["grupo"] = asignar_grupos(aceptados["id_externo"].tolist(), estratos, pct, semilla)
    n_t = int((aceptados["grupo"] == "tratamiento").sum())
    n_c = len(aceptados) - n_t
    if n_t < 1 or n_c < 1:
        raise ErrorAcciones(t("acciones.error.grupos_chicos", n=len(aceptados), pct=pct))

    calidad = _calibracion_modelo(conn, meta["modelo_version"])
    proy = proyectar(aceptados, accion, cfg, version_cfg, meta, calidad)
    p = proy["tasa_esperada_sin_accion"]["total"]
    emd = efecto_minimo_detectable(p, n_t, n_c)
    esperado = proy["esperado"]["efecto_esperado"]
    aviso = None
    if esperado < emd:
        from ..puntaje.presentacion import _num
        aviso = t("acciones.aviso_no_concluyente", nt=n_t, nc=n_c, emd=_num(100 * emd), esp=_num(100 * esperado))
    estr = (aceptados.groupby(["banda", "segmento", "grupo"]).size().unstack(fill_value=0)
            .reindex(columns=["tratamiento", "control"], fill_value=0))
    diseno = {"pct_control": pct, "tratamiento": n_t, "control": n_c,
              "estratos": [{"banda": b, "segmento": s, "tratamiento": int(r["tratamiento"]), "control": int(r["control"])}
                           for (b, s), r in estr.iterrows()],
              "efecto_minimo_detectable": round(emd, 6), "efecto_esperado": esperado, "aviso": aviso,
              "puntajes_calculados_en": str(meta["calculado_en"]), "puntuados": n_puntuados, "elegibles": n_elegibles}
    exclusiones = {"total": int(len(excluidos)),
                   "por_motivo": {k: int(v) for k, v in excluidos["motivo_excl"].value_counts().items()}}
    filtros = {"bandas": bandas or [], "segmentos": segmentos or [], "limite": limite,
               "elegibilidad_accion": accion["elegibilidad"]}
    demo = conn.execute("SELECT is_demo FROM tenants").fetchone()["is_demo"]
    numero = conn.execute("SELECT COALESCE(max(numero), 0) + 1 AS n FROM corridas").fetchone()["n"]
    c = conn.execute(
        """INSERT INTO corridas (tenant_id, numero, accion_id, etapa, creada_por, fecha_puntajes, modelo_version,
                                 config_version, pct_control, semilla, filtros, exclusiones, diseno, proyeccion, is_demo)
           VALUES (app_tenant(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (numero, accion["id"], etapa, u["id"], meta["fecha"], meta["modelo_version"], version_cfg, pct, semilla,
         json.dumps(filtros), json.dumps(exclusiones), json.dumps(diseno), json.dumps({**proy, "congelada": False}),
         demo)).fetchone()
    conn.execute(
        """INSERT INTO corrida_entidades (corrida_id, tenant_id, entidad_id, etapa, grupo, banda, segmento, puntaje, motivo, is_demo)
           SELECT %(c)s, app_tenant(), u.e, %(etapa)s, u.g, u.b, u.s, u.p, u.m, %(demo)s
           FROM unnest(%(e)s::uuid[], %(g)s::text[], %(b)s::text[], %(s)s::text[], %(p)s::numeric[], %(m)s::text[])
                AS u(e, g, b, s, p, m)""",
        {"c": c["id"], "etapa": etapa, "demo": demo, "e": list(aceptados["entidad_id"]), "g": list(aceptados["grupo"]),
         "b": list(aceptados["banda"]), "s": list(aceptados["segmento"]), "p": list(aceptados["puntaje"]),
         "m": list(aceptados["motivo"])})
    if len(excluidos):
        conn.execute(
            """INSERT INTO corrida_excluidos (corrida_id, tenant_id, entidad_id, motivo, is_demo)
               SELECT %s, app_tenant(), u.e, u.m, %s FROM unnest(%s::uuid[], %s::text[]) AS u(e, m)""",
            (c["id"], demo, list(excluidos["entidad_id"]), list(excluidos["motivo_excl"])))
    auditar(conn, usuario, "corrida_creada", {"corrida": c["numero"], "accion": accion["codigo"], "version": accion["version"],
                                             "tratamiento": n_t, "control": n_c, "semilla": semilla,
                                             "excluidos": exclusiones})
    return c


# ---------------------------------------------------------------- estados
def pedir_aprobacion(conn, ref, usuario: str) -> dict:
    usuarios.exigir(conn, usuario, "crear_corrida")
    c = obtener(conn, ref)
    _exigir_estado(c, ("borrador",), "pedir aprobación")
    c = conn.execute("UPDATE corridas SET estado = 'pendiente_aprobacion' WHERE id = %s RETURNING *", (c["id"],)).fetchone()
    auditar(conn, usuario, "corrida_aprobacion_pedida", {"corrida": c["numero"]})
    return c


def cola_aprobacion(conn) -> list[dict]:
    return listar(conn, "pendiente_aprobacion")


def aprobar(conn, ref, usuario: str) -> dict:
    """Congela la proyección con la configuración vigente. Sin valor económico, con puntajes viejos o sin los
    cuatro ojos que pida la configuración, no se aprueba."""
    u = usuarios.exigir(conn, usuario, "aprobar")
    c = obtener(conn, ref)
    _exigir_estado(c, ("pendiente_aprobacion",), "aprobar")
    version_cfg, cfg = config_activa(conn)
    if cfg.acciones.cuatro_ojos and u["id"] == c["creada_por"]:
        raise ErrorAcciones(t("acciones.error.cuatro_ojos"))
    if cfg.valor_economico.valor_por_resultado.get(c["etapa"]) is None:
        raise ErrorAcciones(t("acciones.error.sin_valor", etapa=c["etapa"]))
    edad = _edad_dias(c["diseno"]["puntajes_calculados_en"])
    if edad > cfg.acciones.antiguedad_max_puntajes_dias:
        raise ErrorAcciones(t("acciones.error.puntajes_viejos", dias=edad, max=cfg.acciones.antiguedad_max_puntajes_dias))
    accion = conn.execute("SELECT * FROM acciones_catalogo WHERE id = %s", (c["accion_id"],)).fetchone()
    m = miembros(conn, c["id"])
    m["puntaje"] = m["puntaje"].astype(float)
    meta = {"modelo_version": c["modelo_version"], "fecha": c["fecha_puntajes"]}
    proy = proyectar(m, accion, cfg, version_cfg, meta, _calibracion_modelo(conn, c["modelo_version"]))
    proy.update({"congelada": True, "congelada_en": datetime.now(timezone.utc).isoformat(), "semilla": c["semilla"],
                 "aprobada_por": usuario, "creada_por_misma_persona": u["id"] == c["creada_por"],
                 "efecto_minimo_detectable": c["diseno"]["efecto_minimo_detectable"], "aviso": c["diseno"]["aviso"]})
    c = conn.execute("""UPDATE corridas SET estado = 'aprobada', aprobada_por = %s, aprobada_en = now(), proyeccion = %s
                        WHERE id = %s RETURNING *""", (u["id"], json.dumps(proy), c["id"])).fetchone()
    auditar(conn, usuario, "corrida_aprobada", {"corrida": c["numero"], "config_version": version_cfg,
                                               "roi_esperado": proy["esperado"]["roi_esperado"],
                                               "misma_persona_que_creo": proy["creada_por_misma_persona"]})
    return c


def cancelar(conn, ref, usuario: str, motivo: str) -> dict:
    u = usuarios.exigir(conn, usuario, "cancelar")
    c = obtener(conn, ref)
    _exigir_estado(c, ("borrador", "pendiente_aprobacion", "aprobada"), "cancelar")
    if u["rol"] == "analista" and (c["estado"] == "aprobada" or c["creada_por"] != u["id"]):
        raise ErrorAcciones(t("acciones.error.permiso", usuario=usuario, rol=t("acciones.rol.analista"),
                              permiso="cancelar corridas aprobadas o creadas por otra persona"))
    if not (motivo or "").strip():
        raise ErrorAcciones("Para cancelar hace falta un motivo")
    c = conn.execute("""UPDATE corridas SET estado = 'cancelada', cancelada_por = %s, motivo_cancelacion = %s
                        WHERE id = %s RETURNING *""", (u["id"], motivo.strip(), c["id"])).fetchone()
    auditar(conn, usuario, "corrida_cancelada", {"corrida": c["numero"], "motivo": motivo.strip()})
    return c


def lanzar(conn, ref, usuario: str, fecha: date, hoy: date | None = None) -> dict:
    """La marca una persona con la fecha real en que el cliente ejecutó la acción (D8). Desde esa fecha corre la
    ventana de medición del hito 4. DataPulso no ejecuta nada en el sistema del cliente."""
    u = usuarios.exigir(conn, usuario, "lanzar")
    c = obtener(conn, ref)
    _exigir_estado(c, ("aprobada",), "marcar como lanzada")
    hoy = hoy or date.today()
    desde = c["aprobada_en"].date()
    if not (desde <= fecha <= hoy):
        raise ErrorAcciones(t("acciones.error.fecha_lanzamiento", desde=desde, hasta=hoy))
    c = conn.execute("""UPDATE corridas SET estado = 'lanzada', lanzada_por = %s, fecha_lanzamiento = %s
                        WHERE id = %s RETURNING *""", (u["id"], fecha, c["id"])).fetchone()
    auditar(conn, usuario, "corrida_lanzada", {"corrida": c["numero"], "fecha_lanzamiento": str(fecha)})
    return c
