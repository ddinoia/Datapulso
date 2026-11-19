"""Definición de las tres industrias de datos de prueba (servicios, salud, financiera).

Todo lo que cambia entre industrias vive acá como datos: tipos de evento, tasas, planes, nombres de columnas
que "usaría el cliente" (para ejercitar el mapeo de verdad) y plantillas de mapeo.
Salud: SOLO eventos administrativos (altas, turnos, uso de servicios, cuotas). Nada clínico.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TipoEvento:
    tipo: str
    tasa_mensual: float          # eventos por cliente por mes (base)
    pendiente_riesgo: float      # exp(pendiente * z): >0 los clientes de más riesgo generan más
    respuesta_pre_baja: str      # 'cae' (usa menos antes de irse) | 'sube' (más reclamos antes de irse) | 'plano'
    monto_mediana: float | None = None   # None = sin monto
    canales: tuple = ()


@dataclass(frozen=True)
class Industria:
    nombre: str
    n_clientes: int
    pct_empresas: float                  # empresas cada 100 clientes personas
    pct_con_empresa: float               # % de personas que pertenecen a una empresa
    hazard_base_mensual: float           # prob. mensual de baja de un cliente medio
    atributos: dict                      # nombre -> [(valor, peso, efecto_en_riesgo)]
    eventos: tuple
    cuota_por_atributo: tuple            # (atributo, {valor: monto}) o (None, {"_": monto})
    dias_vencimiento: int
    mora_base_logit: float
    moneda: str
    tablas: dict                         # nombres de tablas en la base del cliente
    renombres: dict                      # columnas internas -> columnas "del cliente", por tabla
    zona: str = "America/Argentina/Buenos_Aires"


SERVICIOS = Industria(
    nombre="servicios", n_clientes=3000, pct_empresas=2.0, pct_con_empresa=0.15, hazard_base_mensual=0.028,
    atributos={
        "plan": [("basico", 0.5, 0.30), ("estandar", 0.35, 0.0), ("premium", 0.15, -0.35)],
        "canal_alta": [("web", 0.45, 0.0), ("tienda", 0.35, -0.1), ("telefono", 0.20, 0.25)],
        "ciudad": [("CABA", 0.35, 0.0), ("Cordoba", 0.2, 0.0), ("Rosario", 0.15, 0.0), ("Mendoza", 0.15, 0.0), ("Tucuman", 0.15, 0.05)],
    },
    eventos=(
        TipoEvento("uso", 14.0, -0.30, "cae", None, ("app", "web")),
        TipoEvento("reclamo", 0.25, 0.50, "sube", None, ("telefono", "web")),
        TipoEvento("contacto_soporte", 0.40, 0.30, "sube", None, ("telefono", "chat")),
    ),
    cuota_por_atributo=("plan", {"basico": 12000, "estandar": 22000, "premium": 38000}),
    dias_vencimiento=10, mora_base_logit=-2.2, moneda="ARS",
    tablas={"entidades": "clientes", "eventos": "movimientos", "pagos": "facturas"},
    renombres={
        "entidades": {"id_externo": "nro_cliente", "tipo": "tipo_cliente", "empresa": "id_empresa", "estado": "estado"},
        "eventos": {"id_externo": "id_evento", "entidad": "nro_cliente", "ts": "fecha_hora", "tipo": "tipo_evento",
                    "monto": "importe", "canal": "canal"},
        "pagos": {"comprobante": "nro_factura", "entidad": "nro_cliente", "fecha_emision": "emision",
                  "fecha_vencimiento": "vencimiento", "monto": "importe", "fecha_pago": "fecha_pago",
                  "monto_pagado": "importe_pagado"},
    },
)

SALUD = Industria(
    nombre="salud", n_clientes=2500, pct_empresas=4.0, pct_con_empresa=0.35, hazard_base_mensual=0.018,
    atributos={
        "plan": [("joven", 0.3, 0.25), ("familiar", 0.45, -0.1), ("premium", 0.25, -0.3)],
        "grupo_etario": [("18-30", 0.2, 0.3), ("31-45", 0.3, 0.0), ("46-60", 0.3, -0.1), ("60+", 0.2, -0.2)],
        "localidad": [("CABA", 0.4, 0.0), ("GBA Norte", 0.2, 0.0), ("GBA Sur", 0.2, 0.05), ("La Plata", 0.1, 0.0), ("Interior", 0.1, 0.1)],
    },
    eventos=(
        TipoEvento("turno", 1.1, -0.25, "cae", None, ("app", "telefono", "web")),
        TipoEvento("uso_servicio", 0.9, -0.20, "cae", None, ("prestador", "farmacia")),
        TipoEvento("reclamo", 0.08, 0.50, "sube", None, ("telefono", "web")),
    ),
    cuota_por_atributo=("plan", {"joven": 55000, "familiar": 90000, "premium": 150000}),
    dias_vencimiento=15, mora_base_logit=-2.0, moneda="ARS",
    tablas={"entidades": "afiliados", "eventos": "prestaciones", "pagos": "cuotas"},
    renombres={
        "entidades": {"id_externo": "nro_afiliado", "tipo": "tipo", "empresa": "id_empresa", "fecha_alta": "fecha_afiliacion",
                      "estado": "estado_afiliacion", "fecha_baja": "fecha_baja"},
        "eventos": {"id_externo": "id_registro", "entidad": "nro_afiliado", "ts": "fecha_hora", "tipo": "tipo_registro",
                    "monto": "importe", "canal": "canal"},
        "pagos": {"comprobante": "nro_cuota", "entidad": "nro_afiliado", "fecha_emision": "fecha_emision",
                  "fecha_vencimiento": "fecha_vto", "monto": "monto_cuota", "fecha_pago": "fecha_cobro",
                  "monto_pagado": "monto_cobrado"},
    },
)

FINANCIERA = Industria(
    nombre="financiera", n_clientes=2000, pct_empresas=1.5, pct_con_empresa=0.10, hazard_base_mensual=0.022,
    atributos={
        "producto": [("prestamo_personal", 0.45, 0.1), ("tarjeta", 0.40, -0.1), ("adelanto_haberes", 0.15, 0.3)],
        "segmento_ingreso": [("bajo", 0.35, 0.25), ("medio", 0.45, 0.0), ("alto", 0.20, -0.3)],
        "sucursal": [("Centro", 0.3, 0.0), ("Norte", 0.25, 0.0), ("Sur", 0.25, 0.0), ("Digital", 0.2, 0.1)],
    },
    eventos=(
        TipoEvento("consumo", 5.0, -0.30, "cae", 18000.0, ("tarjeta", "app")),
        TipoEvento("consulta_app", 6.0, -0.25, "cae", None, ("app",)),
        TipoEvento("reclamo", 0.10, 0.50, "sube", None, ("telefono", "web")),
    ),
    cuota_por_atributo=("producto", {"prestamo_personal": 42000, "tarjeta": 28000, "adelanto_haberes": 65000}),
    dias_vencimiento=20, mora_base_logit=-1.2, moneda="ARS",
    tablas={"entidades": "clientes", "eventos": "operaciones", "pagos": "cuotas"},
    renombres={
        "entidades": {"id_externo": "nro_cliente", "tipo": "tipo_persona", "empresa": "cuit_empresa_convenio",
                      "estado": "situacion"},
        "eventos": {"id_externo": "id_operacion", "entidad": "nro_cliente", "ts": "fecha_hora", "tipo": "tipo_operacion",
                    "monto": "importe", "canal": "canal"},
        "pagos": {"comprobante": "nro_cuota", "entidad": "nro_cliente", "fecha_emision": "emision",
                  "fecha_vencimiento": "vencimiento", "monto": "importe_cuota", "fecha_pago": "fecha_acreditacion",
                  "monto_pagado": "importe_acreditado"},
    },
)

INDUSTRIAS = {i.nombre: i for i in (SERVICIOS, SALUD, FINANCIERA)}


def nombre_col(ind: Industria, tabla: str, interna: str) -> str:
    return ind.renombres[tabla].get(interna, interna)


def plantilla_mapeos(ind: Industria) -> dict:
    """Mapeos listos para crear las 3 fuentes (CSV o base de datos) de una industria."""
    n = lambda t, c: nombre_col(ind, t, c)
    attrs = list(ind.atributos.keys())
    return {
        "entidades": {
            "destino": "entidades",
            "columnas": {"id_externo": n("entidades", "id_externo"), "tipo": n("entidades", "tipo"),
                         "parent_id_externo": n("entidades", "empresa"), "fecha_alta": n("entidades", "fecha_alta"),
                         "fecha_baja": n("entidades", "fecha_baja"), "estado_externo": n("entidades", "estado")},
            "atributos": attrs,
        },
        "eventos": {
            "destino": "eventos",
            "columnas": {"id_externo": n("eventos", "id_externo"), "entidad_id_externo": n("eventos", "entidad"),
                         "ts": n("eventos", "ts"), "tipo": n("eventos", "tipo"), "monto": n("eventos", "monto"),
                         "canal": n("eventos", "canal")},
            "atributos": [],
        },
        "pagos": {
            "destino": "pagos",
            "columnas": {"comprobante": n("pagos", "comprobante"), "entidad_id_externo": n("pagos", "entidad"),
                         "fecha_emision": n("pagos", "fecha_emision"), "fecha_vencimiento": n("pagos", "fecha_vencimiento"),
                         "monto": n("pagos", "monto"), "fecha_pago": n("pagos", "fecha_pago"),
                         "monto_pagado": n("pagos", "monto_pagado")},
            "atributos": [],
        },
    }
