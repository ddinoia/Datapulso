"""Acciones de ejemplo por industria (datos de tenant para arrancar un piloto, no textos de producto).

El impacto esperado es una reducción RELATIVA de la tasa del evento adverso (D1): 0,15 = de cada 100 bajas
esperadas sin acción se evitan 15. Son supuestos, no medidos: quedan rotulados así hasta que el hito 4 mida.
Salud: solo acciones administrativas (turnos, cuotas, atención al afiliado). Nada clínico.
"""

_COMUNES = {"frecuencia_max_dias": 30, "modifica_sistema_cliente": False}


def _a(codigo, nombre, etapa, tipo, canal, costo, impacto, bandas=None, plantilla=None, **extra):
    return {**_COMUNES, "codigo": codigo, "nombre": nombre, "etapa": etapa, "tipo": tipo, "canal": canal,
            "costo_unitario": costo, "impacto_esperado": impacto, "plantilla": plantilla,
            "elegibilidad": {"bandas": bandas or []}, **extra}


PLANTILLAS = {
    "servicios": [
        _a("RET_LLAMADA", "Llamado de retención", "retencion", "lista_llamadas", "telefono", 1500, 0.15, ["alta"],
           "Llamar para conocer el motivo de la menor actividad y ofrecer ayuda.", frecuencia_max_dias=60),
        _a("RET_BENEFICIO_PLAN", "Beneficio en el plan por 3 meses", "retencion", "oferta_beneficio", "email", 4000, 0.20,
           ["alta", "media"], "Ofrecer un beneficio en el plan vigente.", modifica_sistema_cliente=True, frecuencia_max_dias=180),
        _a("RET_MENSAJE", "Mensaje con novedades del servicio", "retencion", "mensaje", "app", 40, 0.04, ["alta", "media"],
           "Recordar funciones del servicio que el cliente no usa."),
        _a("COB_RECORDATORIO", "Recordatorio de vencimiento", "cobranza", "mensaje", "sms", 30, 0.10, [],
           "Recordar la fecha de vencimiento y los medios de pago.", frecuencia_max_dias=20),
        _a("COB_LLAMADA", "Llamado de cobranza preventiva", "cobranza", "lista_llamadas", "telefono", 1200, 0.25, ["alta"],
           "Llamar antes del vencimiento para confirmar el pago.", frecuencia_max_dias=30),
        _a("ACT_BIENVENIDA", "Mensaje de bienvenida con primeros pasos", "activacion", "mensaje", "app", 40, 0.10, [],
           "Explicar cómo empezar a usar el servicio."),
        _a("ACT_ONBOARDING", "Llamado de acompañamiento inicial", "activacion", "tarea_humana", "telefono", 1500, 0.20,
           ["alta"], "Tarea para el equipo de atención: acompañar el primer uso."),
    ],
    "salud": [
        _a("RET_RECORDATORIO_TURNO", "Recordatorio de turnos y cartilla disponibles", "retencion", "mensaje", "app", 40, 0.05,
           ["alta", "media"], "Recordar cómo pedir turnos y consultar la cartilla de prestadores."),
        _a("RET_ATENCION_AFILIADO", "Llamado de atención al afiliado", "retencion", "lista_llamadas", "telefono", 1500, 0.12,
           ["alta"], "Llamado administrativo: consultas sobre el plan, credencial y trámites.", frecuencia_max_dias=60),
        _a("COB_AVISO_CUOTA", "Aviso de vencimiento de cuota", "cobranza", "mensaje", "sms", 30, 0.10, [],
           "Avisar la fecha de vencimiento de la cuota y los medios de pago.", frecuencia_max_dias=20),
        _a("COB_LLAMADA_CUOTA", "Llamado por cuota próxima a vencer", "cobranza", "lista_llamadas", "telefono", 1200, 0.20,
           ["alta"], "Llamado administrativo por la cuota."),
        _a("ACT_BIENVENIDA_AFILIADO", "Bienvenida: credencial y cómo pedir turnos", "activacion", "mensaje", "email", 40, 0.10,
           [], "Enviar la credencial digital y explicar cómo pedir el primer turno."),
    ],
    "financiera": [
        _a("RET_LLAMADA_EJECUTIVO", "Llamado del ejecutivo de cuenta", "retencion", "lista_llamadas", "telefono", 1800, 0.15,
           ["alta"], "Llamar para revisar el uso del producto.", frecuencia_max_dias=60),
        _a("RET_BENEFICIO_TASA", "Beneficio en la tasa o el límite", "retencion", "oferta_beneficio", "app", 6000, 0.20,
           ["alta", "media"], "Ofrecer una mejora de condiciones.", modifica_sistema_cliente=True, frecuencia_max_dias=180),
        _a("COB_RECORDATORIO_VTO", "Recordatorio de vencimiento de cuota", "cobranza", "mensaje", "sms", 30, 0.12, [],
           "Recordar el vencimiento y los medios de pago.", frecuencia_max_dias=20),
        _a("COB_ACUERDO_PAGO", "Propuesta de acuerdo de pago", "cobranza", "tarea_humana", "telefono", 2500, 0.30, ["alta"],
           "Tarea para el equipo de cobranzas: proponer un plan de pagos.", modifica_sistema_cliente=True),
        _a("ACT_BIENVENIDA_APP", "Bienvenida y primeros pasos en la app", "activacion", "mensaje", "app", 40, 0.10, [],
           "Explicar cómo hacer el primer consumo."),
    ],
}
