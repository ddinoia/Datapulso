"""Último cálculo de puntajes de una etapa, con lo que necesitan la recomendación y las corridas."""
import pandas as pd
from ..textos import t
from ..puntaje.presentacion import texto_driver
from . import ErrorAcciones

SIN_DATO = "(sin dato)"


def puntajes_etapa(conn, etapa: str, cfg) -> tuple[pd.DataFrame, dict]:
    """(clientes, metadatos). Exige puntajes con probabilidad (método modelo): la regla simple no proyecta."""
    meta = conn.execute("""SELECT fecha, modelo_version, config_version, metodo, max(creado_en) AS calculado_en, count(*) AS n
                           FROM puntajes WHERE etapa = %s AND fecha = (SELECT max(fecha) FROM puntajes WHERE etapa = %s)
                           GROUP BY fecha, modelo_version, config_version, metodo""", (etapa, etapa)).fetchall()
    if not meta:
        raise ErrorAcciones(t("acciones.error.sin_puntajes", etapa=etapa))
    meta = meta[0]
    if meta["metodo"] != "modelo":
        raise ErrorAcciones(t("acciones.error.sin_probabilidad", etapa=etapa))
    seg = cfg.segmento_atributo
    cons = cfg.politica_contacto.atributo_consentimiento
    rows = conn.execute(
        """SELECT p.entidad_id, e.id_externo, emp.id_externo AS empresa, p.banda, p.puntaje::float AS puntaje, p.drivers,
                  e.atributos->>%(seg)s AS segmento, e.atributos->>%(cons)s AS consentimiento
           FROM puntajes p JOIN entidades e ON e.id = p.entidad_id
           LEFT JOIN entidades emp ON emp.id = e.parent_id
           WHERE p.etapa = %(etapa)s AND p.fecha = %(f)s
           ORDER BY p.puntaje DESC, e.id_externo""",
        {"seg": seg or "", "cons": cons, "etapa": etapa, "f": meta["fecha"]}).fetchall()
    df = pd.DataFrame(rows)
    df["segmento"] = df["segmento"].fillna(SIN_DATO) if seg else SIN_DATO
    df["motivo"] = [texto_driver(d[0]) if d else "" for d in df["drivers"]]
    return df, dict(meta)
