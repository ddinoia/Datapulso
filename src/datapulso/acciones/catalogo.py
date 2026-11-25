"""Catálogo de acciones (sección 7 de PRODUCTO.md) en tabla propia, con versiones inmutables.

Por qué tabla propia y no dentro de `config_versions` (decisión pendiente para Marcelo): el catálogo cambia más
seguido que la configuración (costos, impacto, plantillas), cada corrida tiene que apuntar a la versión exacta
de la acción que se aprobó, y meterlo en la configuración haría que cada ajuste de un texto cree una versión de
configuración nueva. Se mantiene el mismo principio: nunca se edita una fila, se inserta la versión siguiente.
"""
from typing import Literal
from pydantic import BaseModel, Field
from ..tenants import auditar
from . import ErrorAcciones
from .plantillas import PLANTILLAS

BANDAS = ("alta", "media", "baja")


class Elegibilidad(BaseModel):
    bandas: list[Literal["alta", "media", "baja"]] = Field(default_factory=list, description="vacío = todas")
    segmentos: list[str] = Field(default_factory=list, description="vacío = todos")


class AccionDef(BaseModel):
    codigo: str = Field(pattern=r"^[A-Z0-9_]{3,40}$")
    nombre: str
    etapa: Literal["retencion", "cobranza", "activacion"]
    tipo: Literal["mensaje", "oferta_beneficio", "tarea_humana", "lista_llamadas", "webhook"]
    canal: str
    plantilla: str | None = None
    costo_unitario: float = Field(ge=0)
    elegibilidad: Elegibilidad = Field(default_factory=Elegibilidad)
    frecuencia_max_dias: int = Field(30, ge=0)
    modifica_sistema_cliente: bool = False
    impacto_esperado: float = Field(ge=0, le=1, description="reducción relativa de la tasa del evento adverso (D1)")
    impacto_origen: Literal["supuesto", "medido"] = "supuesto"
    activa: bool = True


def guardar(conn, accion: AccionDef | dict, actor: str) -> dict:
    """Inserta la versión siguiente del código (la primera si no existía)."""
    a = accion if isinstance(accion, AccionDef) else AccionDef.model_validate(accion)
    v = conn.execute("SELECT COALESCE(max(version), 0) + 1 AS v FROM acciones_catalogo WHERE codigo = %s",
                     (a.codigo,)).fetchone()["v"]
    demo = conn.execute("SELECT is_demo FROM tenants").fetchone()["is_demo"]
    fila = conn.execute(
        """INSERT INTO acciones_catalogo (tenant_id, codigo, version, nombre, etapa, tipo, canal, plantilla, costo_unitario,
                 elegibilidad, frecuencia_max_dias, modifica_sistema_cliente, impacto_esperado, impacto_origen, activa,
                 is_demo, creado_por)
           VALUES (app_tenant(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (a.codigo, v, a.nombre, a.etapa, a.tipo, a.canal, a.plantilla, a.costo_unitario, a.elegibilidad.model_dump_json(),
         a.frecuencia_max_dias, a.modifica_sistema_cliente, a.impacto_esperado, a.impacto_origen, a.activa, demo, actor)).fetchone()
    auditar(conn, actor, "accion_version_creada", {"codigo": a.codigo, "version": v, "impacto_esperado": a.impacto_esperado,
                                                   "impacto_origen": a.impacto_origen, "activa": a.activa})
    return fila


def vigentes(conn, etapa: str | None = None) -> list[dict]:
    """Última versión de cada código, solo si está activa."""
    return conn.execute(
        """SELECT * FROM (SELECT DISTINCT ON (codigo) * FROM acciones_catalogo ORDER BY codigo, version DESC) a
           WHERE activa AND (%(e)s::text IS NULL OR etapa = %(e)s) ORDER BY etapa, codigo""", {"e": etapa}).fetchall()


def vigente(conn, codigo: str) -> dict:
    for a in vigentes(conn):
        if a["codigo"] == codigo:
            return a
    raise ErrorAcciones(f"No hay una acción activa con código {codigo}")


def cargar_plantilla(conn, industria: str, actor: str) -> list[dict]:
    if industria not in PLANTILLAS:
        raise ErrorAcciones(f"No hay plantilla de acciones para la industria {industria}")
    existentes = {a["codigo"] for a in vigentes(conn)}
    return [guardar(conn, a, actor) for a in PLANTILLAS[industria] if a["codigo"] not in existentes]
