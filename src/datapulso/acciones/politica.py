"""Política de contacto (D6), aplicada ANTES de asignar tratamiento y control.

Orden de los motivos (cada cliente queda afuera por el primero que aplique):
  1. corrida_abierta     ya está en otra corrida abierta de la misma etapa (D4: contaminaría la medición)
  2. lista_exclusion     su id externo está en `politica_contacto.exclusiones`
  3. sin_consentimiento  si `consentimiento_requerido`, el atributo de consentimiento no dice que sí
  4. frecuencia_maxima   recibió esta misma acción (tratamiento) hace menos de `frecuencia_max_dias`
  5. tope_contactos      ya tiene `max_contactos_por_cliente_30d` contactos de tratamiento en los últimos 30 días
Los clientes de control no cuentan como contactados (D5). La fecha de contacto de una corrida es su fecha de
lanzamiento, o la de aprobación si todavía no se lanzó. Los horarios no filtran: van como dato en la exportación.
"""
from datetime import date, timedelta
import pandas as pd

ABIERTAS = ("borrador", "pendiente_aprobacion", "aprobada", "lanzada", "medicion_en_curso")
CONTACTADAS = ("aprobada", "lanzada", "medicion_en_curso", "cerrada")
SI = {"si", "sí", "s", "true", "1", "yes"}


def aplicar(conn, cfg, accion: dict, df: pd.DataFrame, hoy: date | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(aceptados, excluidos con columna `motivo`)."""
    hoy = hoy or date.today()
    pol = cfg.politica_contacto
    ids = list(df["entidad_id"])
    en_abierta = {r["entidad_id"] for r in conn.execute(
        """SELECT DISTINCT ce.entidad_id FROM corrida_entidades ce JOIN corridas c ON c.id = ce.corrida_id
           WHERE ce.etapa = %s AND c.estado = ANY(%s) AND ce.entidad_id = ANY(%s)""",
        (accion["etapa"], list(ABIERTAS), ids)).fetchall()}
    misma_accion = {r["entidad_id"] for r in conn.execute(
        """SELECT DISTINCT ce.entidad_id FROM corrida_entidades ce JOIN corridas c ON c.id = ce.corrida_id
           JOIN acciones_catalogo a ON a.id = c.accion_id
           WHERE a.codigo = %s AND ce.grupo = 'tratamiento' AND c.estado = ANY(%s)
             AND COALESCE(c.fecha_lanzamiento, c.aprobada_en::date) > %s AND ce.entidad_id = ANY(%s)""",
        (accion["codigo"], list(CONTACTADAS), hoy - timedelta(days=accion["frecuencia_max_dias"]), ids)).fetchall()}
    contactos = {r["entidad_id"]: r["n"] for r in conn.execute(
        """SELECT ce.entidad_id, count(*) AS n FROM corrida_entidades ce JOIN corridas c ON c.id = ce.corrida_id
           WHERE ce.grupo = 'tratamiento' AND c.estado = ANY(%s)
             AND COALESCE(c.fecha_lanzamiento, c.aprobada_en::date) > %s AND ce.entidad_id = ANY(%s)
           GROUP BY ce.entidad_id""", (list(CONTACTADAS), hoy - timedelta(days=30), ids)).fetchall()}
    excl = set(pol.exclusiones)

    def motivo(r) -> str | None:
        if r.entidad_id in en_abierta:
            return "corrida_abierta"
        if r.id_externo in excl:
            return "lista_exclusion"
        if pol.consentimiento_requerido and str(r.consentimiento or "").strip().lower() not in SI:
            return "sin_consentimiento"
        if r.entidad_id in misma_accion:
            return "frecuencia_maxima"
        if contactos.get(r.entidad_id, 0) >= pol.max_contactos_por_cliente_30d:
            return "tope_contactos"
        return None

    m = [motivo(r) for r in df.itertuples()]
    df = df.assign(motivo_exclusion=m)
    return (df[df["motivo_exclusion"].isna()].drop(columns="motivo_exclusion"),
            df[df["motivo_exclusion"].notna()].rename(columns={"motivo_exclusion": "motivo_excl"}))
