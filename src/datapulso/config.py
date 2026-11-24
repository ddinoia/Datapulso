"""Configuración por tenant (versionada, sin deploy). Modelo validado + plantillas por industria.

Las 5 etapas son plantillas de configuración, no código: cada tenant define el evento objetivo
de cada etapa como una regla sobre eventos.
"""
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator

ETAPAS = ["alta", "activacion", "uso_valor", "retencion", "cobranza"]


class Regla(BaseModel):
    """Regla de "alcanzó la etapa" sobre eventos.
    desde_alta: al menos `min_eventos` en los primeros `dias` desde el alta.
    ventana_reciente: al menos `min_eventos` en los últimos `dias` antes de la fecha de corte."""
    eventos_tipo: list[str] = Field(default_factory=list, description="vacío = cualquier tipo")
    min_eventos: int = Field(1, ge=1)
    modo: Literal["desde_alta", "ventana_reciente"]
    dias: int = Field(..., ge=1, le=730)


class EtapaCfg(BaseModel):
    activa: bool = True
    evento_objetivo: Regla | None = None
    ventana_medicion_dias: int = Field(30, ge=1, le=365)
    # Hito 2. Horizonte del puntaje: "probabilidad de que pase X en los próximos N días".
    # Si falta (versiones de configuración anteriores), se usa la ventana de medición: el puntaje predice
    # lo mismo que después se mide. En activación y uso y valor el horizonte sale de la regla (`dias`).
    ventana_prediccion_dias: int | None = Field(None, ge=7, le=365)


class Vocabulario(BaseModel):
    cliente: str = "Cliente"
    clientes: str = "Clientes"
    tenant: str = "Organización"
    accion: str = "Acción"
    corrida: str = "Corrida"
    control: str = "Control"
    puntaje: str = "Puntaje"
    etapas: dict[str, str] = Field(default_factory=lambda: {
        "alta": "Alta", "activacion": "Activación", "uso_valor": "Uso y valor",
        "retencion": "Retención", "cobranza": "Cobranza"})


class CobranzaCfg(BaseModel):
    gracia_dias: int = Field(0, ge=0, le=60, description="días de tolerancia para considerar 'pagó en término'")


class ValorEconomico(BaseModel):
    """Obligatorio para calcular ROI (hito 4). Mientras sea None, el ROI queda 'no configurado'."""
    valor_por_resultado: dict[str, float | None] = Field(default_factory=dict)
    costo_por_contacto: dict[str, float | None] = Field(default_factory=dict)


class PoliticaContacto(BaseModel):
    max_contactos_por_cliente_30d: int = Field(2, ge=0)
    consentimiento_requerido: bool = True
    exclusiones: list[str] = Field(default_factory=list)


class ConfigTenant(BaseModel):
    schema_version: int = 1
    vocabulario: Vocabulario = Field(default_factory=Vocabulario)
    etapas: dict[str, EtapaCfg]
    cobranza: CobranzaCfg = Field(default_factory=CobranzaCfg)
    segmento_atributo: str | None = Field(None, description="atributo de la entidad usado para segmentar")
    control_pct: float = Field(10.0, gt=0, lt=50)
    control_pct_cobranza: float = Field(5.0, gt=0, lt=50)
    bandas: Literal["terciles"] = "terciles"
    valor_economico: ValorEconomico = Field(default_factory=ValorEconomico)
    politica_contacto: PoliticaContacto = Field(default_factory=PoliticaContacto)

    @field_validator("etapas")
    @classmethod
    def _etapas_validas(cls, v):
        for k in v:
            if k not in ETAPAS:
                raise ValueError(f"etapa desconocida: {k}. Válidas: {ETAPAS}")
        return v

    @model_validator(mode="after")
    def _reglas_obligatorias(self):
        for k, e in self.etapas.items():
            if k in ("uso_valor", "retencion", "activacion") and e.activa and e.evento_objetivo is None:
                raise ValueError(f"la etapa activa '{k}' necesita un evento_objetivo")
        return self


def pendientes(cfg: ConfigTenant) -> list[str]:
    """Qué falta para operar completo. El ROI es obligatorio (decisión de producto)."""
    out = []
    for k, e in cfg.etapas.items():
        if e.activa and cfg.valor_economico.valor_por_resultado.get(k) is None:
            out.append(f"Falta el valor económico de un resultado en la etapa '{k}': sin esto no se calcula ROI.")
    if not cfg.valor_economico.costo_por_contacto:
        out.append("Falta el costo por contacto por canal: sin esto no se calcula ROI.")
    return out


# ---- Plantillas por industria (datos de prueba y punto de partida de pilotos) ----

def plantilla(industria: str) -> ConfigTenant:
    if industria == "servicios":
        return ConfigTenant(
            vocabulario=Vocabulario(),
            segmento_atributo="plan",
            etapas={
                "alta": EtapaCfg(),
                "activacion": EtapaCfg(evento_objetivo=Regla(eventos_tipo=["uso"], min_eventos=3, modo="desde_alta", dias=30)),
                "uso_valor": EtapaCfg(evento_objetivo=Regla(eventos_tipo=["uso"], min_eventos=8, modo="ventana_reciente", dias=30), ventana_medicion_dias=60),
                "retencion": EtapaCfg(evento_objetivo=Regla(eventos_tipo=["uso"], min_eventos=1, modo="ventana_reciente", dias=60), ventana_medicion_dias=90),
                "cobranza": EtapaCfg(ventana_medicion_dias=45),
            })
    if industria == "salud":
        return ConfigTenant(
            vocabulario=Vocabulario(cliente="Afiliado", clientes="Afiliados"),
            segmento_atributo="plan",
            etapas={
                "alta": EtapaCfg(),
                "activacion": EtapaCfg(evento_objetivo=Regla(eventos_tipo=["turno", "uso_servicio"], min_eventos=1, modo="desde_alta", dias=60)),
                "uso_valor": EtapaCfg(evento_objetivo=Regla(eventos_tipo=["turno", "uso_servicio"], min_eventos=2, modo="ventana_reciente", dias=90), ventana_medicion_dias=90),
                "retencion": EtapaCfg(evento_objetivo=Regla(eventos_tipo=[], min_eventos=1, modo="ventana_reciente", dias=120), ventana_medicion_dias=90),
                "cobranza": EtapaCfg(ventana_medicion_dias=45),
            })
    if industria == "financiera":
        return ConfigTenant(
            vocabulario=Vocabulario(),
            segmento_atributo="producto",
            etapas={
                "alta": EtapaCfg(),
                "activacion": EtapaCfg(evento_objetivo=Regla(eventos_tipo=["consumo"], min_eventos=1, modo="desde_alta", dias=45)),
                "uso_valor": EtapaCfg(evento_objetivo=Regla(eventos_tipo=["consumo"], min_eventos=4, modo="ventana_reciente", dias=30), ventana_medicion_dias=60),
                "retencion": EtapaCfg(evento_objetivo=Regla(eventos_tipo=["consumo", "consulta_app"], min_eventos=1, modo="ventana_reciente", dias=60), ventana_medicion_dias=90),
                "cobranza": EtapaCfg(ventana_medicion_dias=30),
            }, cobranza=CobranzaCfg(gracia_dias=3))
    raise ValueError(f"industria sin plantilla: {industria} (disponibles: servicios, salud, financiera)")
