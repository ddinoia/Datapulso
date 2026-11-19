"""Cómo se lee un puntaje: etiquetas de variables y drivers en lenguaje de negocio (textos en textos/es.json).
La base guarda drivers neutros al idioma (variable, valor, referencia, efecto); el texto se arma al mostrarlos."""
from ..textos import t


def _num(x: float, dec: int = 1) -> str:
    s = f"{x:,.{dec}f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return s[:-2] if dec == 1 and s.endswith(",0") else s


def etiqueta_variable(nombre: str) -> str:
    if "__" in nombre:
        pref, resto = nombre.split("__", 1)
        if pref == "attr":
            return t("variable.atributo", atributo=resto.replace("_", " ").capitalize())
        return t(f"variable.{pref}", tipo=resto)
    return t(f"variable.{nombre}")


def _valor(nombre: str, v) -> str:
    if v is None:
        return "(sin dato)"
    if isinstance(v, str):
        return v
    if nombre.startswith("tendencia__"):
        return f"{(v - 1) * 100:+.0f} %".replace("-", "−")
    if nombre == "pct_tarde_180d":
        return f"{_num(100 * v, 0)} %"
    if nombre == "tiene_empresa" or nombre.startswith("sin90__"):
        return "sí" if v >= 0.5 else "no"
    if nombre.startswith("ticket90__") or nombre == "monto_90d":
        return f"$ {_num(v, 0)}"
    return _num(v)


def texto_driver(d: dict) -> str:
    """'Eventos «uso» en los últimos 90 días: 4 (promedio 38,2) · sube el puntaje'"""
    nombre = d["variable"]
    ref = d.get("referencia")
    ref_txt = (f"más común: {ref}" if isinstance(ref, str) else f"promedio {_valor(nombre, ref)}") if ref is not None else ""
    sentido = t("sentido.sube") if d["efecto"] > 0 else t("sentido.baja")
    return f"{etiqueta_variable(nombre)}: {_valor(nombre, d['valor'])}" + (f" ({ref_txt})" if ref_txt else "") + f" · {sentido}"


def resumen_modelo(m: dict, cliente: str = "cliente") -> list[str]:
    """Líneas para mostrar la calidad de una versión de modelo (`cliente`: término del tenant, en singular)."""
    d, cal = m["definicion"], m["calidad"]
    out = [f"{m['version']} · {t('metodo.' + m['metodo'])}",
           f"  Predice: probabilidad de que el {cliente.lower()} {t('evento_modelado.' + d['evento_modelado'], h=d['horizonte_dias'])}",
           f"  Datos hasta {m['fecha_referencia']} · configuración v{m['config_version']}"]
    if m["motivo_metodo"]:
        out.append(f"  Motivo: {m['motivo_metodo']}")
    bt = cal.get("backtest") or {}
    for k in ("modelo", "regla"):
        if k not in bt:
            continue
        b = bt[k]
        ce = bt["corte_evaluacion"][0]["corte"] if bt.get("corte_evaluacion") else "?"
        auc = _num(b["auc"], 3) if b.get("auc") is not None else "s/d"
        bandas = " · ".join(f"{t('banda.' + x['banda'])} {_num(100 * x['tasa'], 1)} %" for x in b["por_banda"] if x["tasa"] is not None)
        out.append(f"  Backtest {k} (evaluado en el corte {ce}, {b['n']} clientes, tasa del evento "
                   f"{_num(100 * b['tasa_base'], 1)} %): AUC {auc} · {bandas}")
    for a in cal.get("avisos", []):
        out.append(f"  Aviso: {a}")
    return out
