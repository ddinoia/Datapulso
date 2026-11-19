"""Mapeo de columnas de la fuente del cliente a las tablas canónicas (configurable por tenant)."""
from typing import Literal
from pydantic import BaseModel, Field, model_validator

CAMPOS = {
    "entidades": {
        "requeridos": ["id_externo", "fecha_alta"],
        "opcionales": ["tipo", "parent_id_externo", "fecha_baja", "estado_externo"],
    },
    "eventos": {
        "requeridos": ["entidad_id_externo", "ts", "tipo"],
        "opcionales": ["id_externo", "monto", "moneda", "canal", "categoria"],
    },
    "pagos": {
        "requeridos": ["entidad_id_externo", "comprobante", "fecha_emision", "fecha_vencimiento", "monto"],
        "opcionales": ["fecha_pago", "monto_pagado", "moneda"],
    },
}


class Mapeo(BaseModel):
    destino: Literal["entidades", "eventos", "pagos"]
    columnas: dict[str, str] = Field(..., description="campo canónico -> columna de la fuente")
    atributos: list[str] = Field(default_factory=list, description="columnas de la fuente que van al jsonb `atributos`")
    formato_fecha: str | None = Field(None, description="strptime para fechas; vacío = ISO (AAAA-MM-DD)")
    formato_ts: str | None = Field(None, description="strptime para fecha-hora; vacío = ISO")
    separador_decimal: Literal[".", ","] = "."
    valores: dict[str, dict[str, str]] = Field(default_factory=dict, description="campo -> {valor origen: valor canónico}")
    zona_horaria: str | None = Field(None, description="zona de las fechas-hora sin zona; vacío = la del tenant")

    @model_validator(mode="after")
    def _chequear(self):
        c = CAMPOS[self.destino]
        validos = set(c["requeridos"]) | set(c["opcionales"])
        for k in self.columnas:
            if k not in validos:
                raise ValueError(f"'{k}' no es un campo de {self.destino}. Válidos: {sorted(validos)}")
        faltan = [k for k in c["requeridos"] if k not in self.columnas]
        if faltan:
            raise ValueError(f"Faltan campos requeridos para {self.destino}: {faltan}")
        return self
