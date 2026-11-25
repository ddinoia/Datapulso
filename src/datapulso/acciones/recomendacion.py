"""Recomendación por reglas (D3): banda × segmento -> acciones elegibles, ordenadas por ROI esperado por cliente.

ROI esperado por cliente = probabilidad × impacto esperado × valor por resultado − costo unitario.
Determinística y explicable: cada recomendación dice de dónde sale el número. Si falta el valor económico de la
etapa, se ordena por eventos evitados esperados (probabilidad × impacto) y se avisa. La persona puede elegir otra.
Las acciones que cambian algo en el sistema del cliente se recomiendan con la advertencia: nunca se ejecutan solas.
"""
import pandas as pd
from ..textos import t
from ..tenants import config_activa
from . import catalogo
from ..puntaje.presentacion import _num
from .base import puntajes_etapa


def _pesos(v: float) -> str:
    return ("−" if v < 0 else "") + "$ " + _num(abs(v), 0)


def _pct(x: float, dec: int = 1) -> str:
    return _num(100 * x, dec) + " %"


def elegible(accion: dict, banda: str, segmento: str) -> bool:
    el = accion["elegibilidad"] or {}
    return (not el.get("bandas") or banda in el["bandas"]) and (not el.get("segmentos") or segmento in el["segmentos"])


def evaluar(accion: dict, p: float, banda: str, segmento: str, valor: float | None) -> dict:
    i = float(accion["impacto_esperado"])
    c = float(accion["costo_unitario"])
    origen = t("acciones.impacto_" + accion["impacto_origen"])
    evitados = p * i
    if valor is None:
        roi = None
        motivo = t("acciones.motivo_sin_valor", ev=_num(evitados, 3), p=_pct(p), i=_pct(i, 0),
                   origen=origen, banda=banda, segmento=segmento)
    else:
        roi = evitados * valor - c
        motivo = t("acciones.motivo_recomendacion", roi=_pesos(roi), p=_pct(p), i=_pct(i, 0),
                   origen=origen, v=_pesos(valor), c=_pesos(c), banda=banda, segmento=segmento)
    if accion["modifica_sistema_cliente"]:
        motivo += " · " + t("acciones.modifica_sistema")
    return {"codigo": accion["codigo"], "nombre": accion["nombre"], "roi_esperado": roi, "evitados_esperados": evitados,
            "motivo": motivo}


def recomendar(conn, etapa: str, cliente: str | None = None, limite: int = 20) -> dict:
    _, cfg = config_activa(conn)
    acciones = catalogo.vigentes(conn, etapa)
    df, meta = puntajes_etapa(conn, etapa, cfg)
    valor = cfg.valor_economico.valor_por_resultado.get(etapa)
    if cliente:
        df = df[df["id_externo"] == cliente]
    clientes, mejor = [], []
    for r in df.itertuples():
        opciones = [evaluar(a, r.puntaje, r.banda, r.segmento, valor) for a in acciones if elegible(a, r.banda, r.segmento)]
        clave = (lambda o: o["roi_esperado"]) if valor is not None else (lambda o: o["evitados_esperados"])
        opciones.sort(key=lambda o: (-clave(o), o["codigo"]))
        mejor.append(opciones[0]["codigo"] if opciones else None)
        if len(clientes) < limite:
            clientes.append({"id_externo": r.id_externo, "banda": r.banda, "puntaje": r.puntaje, "segmento": r.segmento,
                             "opciones": opciones[:3]})
    resumen = pd.Series([m for m in mejor if m]).value_counts().to_dict()
    return {"etapa": etapa, "fecha_puntajes": str(meta["fecha"]), "modelo_version": meta["modelo_version"],
            "valor_configurado": valor is not None, "acciones": len(acciones), "clientes": clientes,
            "mejor_accion_por_cantidad": resumen, "sin_accion_elegible": sum(1 for m in mejor if m is None)}
