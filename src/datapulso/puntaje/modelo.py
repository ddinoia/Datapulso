"""Modelo explicable (regresión logística sobre variables estandarizadas) y regla simple de respaldo.

Los parámetros se guardan como JSON legible en `modelos.parametros` (sin pickles): cualquiera puede recalcular
un puntaje a mano. Drivers de un cliente: contribución de cada variable al logit respecto del cliente promedio
del entrenamiento (coeficiente × valor estandarizado). Los atributos categóricos suman las contribuciones de sus
niveles y se muestran como una sola variable ("plan: básico"); "ningún evento del tipo" se suma a "eventos del tipo".
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

C_REGULARIZACION = 1.0
N_DRIVERS = 5
SOLO_REGLA = ("eventos_90d", "monto_90d")   # totales: el modelo usa el detalle por tipo de evento


def _transformar(nombre: str, x: pd.Series) -> pd.Series:
    if nombre.startswith("tendencia__"):
        return np.log(x.astype(float))
    if nombre in ("pct_tarde_180d", "tiene_empresa") or nombre.startswith("sin90__"):
        return x.astype(float)
    return np.log1p(x.astype(float).clip(lower=0))


def _diseno(X: pd.DataFrame, numericas: list[str], niveles: dict[str, list[str]]) -> pd.DataFrame:
    """Variables transformadas. Un valor numérico vacío (no definido) queda NaN: se reemplaza por la media del
    entrenamiento al estandarizar, o sea que no mueve el puntaje en ningún sentido."""
    cols = {n: _transformar(n, X[n]) for n in numericas}
    for a, lv in niveles.items():
        v = X[a].fillna("(sin dato)")
        for nivel in lv:
            cols[f"{a}={nivel}"] = (v == nivel).astype(float)
    return pd.DataFrame(cols, index=X.index)


# ---------------------------------------------------------------- modelo
def ajustar(X: pd.DataFrame, y: pd.Series) -> dict:
    numericas = [c for c in X.columns if not c.startswith("attr__") and c not in SOLO_REGLA]
    niveles = {}
    for a in (c for c in X.columns if c.startswith("attr__")):
        cuenta = X[a].fillna("(sin dato)").value_counts()
        niveles[a] = sorted(cuenta[cuenta >= 10].index.tolist())
    D = _diseno(X, numericas, niveles)
    media, escala = D.mean(), D.std(ddof=0)
    usar = escala[escala > 1e-9].index.tolist()          # variables constantes no aportan
    Z = ((D[usar] - media[usar]) / escala[usar]).fillna(0.0)
    lr = LogisticRegression(C=C_REGULARIZACION, max_iter=2000)
    lr.fit(Z.values, y.values.astype(int))
    crudo = {**{n: (None if pd.isna(X[n].mean()) else float(X[n].mean())) for n in numericas},
             **{a: str(X[a].fillna("(sin dato)").mode().iloc[0]) for a in niveles}}
    return {
        "tipo": "regresion_logistica", "C": C_REGULARIZACION,
        "numericas": numericas, "niveles": niveles, "columnas": usar,
        "media": {k: float(media[k]) for k in usar}, "escala": {k: float(escala[k]) for k in usar},
        "coef": {k: float(v) for k, v in zip(usar, lr.coef_[0])}, "intercepto": float(lr.intercept_[0]),
        "referencia": crudo,   # promedio (o nivel más común) del entrenamiento, para mostrar al lado del valor
    }


def _grupo(columna: str) -> str:
    """Variable de negocio a la que se atribuye una columna del diseño: los niveles de un atributo van a su
    atributo, y el indicador "ningún evento" va junto con la cantidad de eventos del mismo tipo (un solo driver
    con el efecto neto, en vez de dos que se contradicen)."""
    base = columna.split("=")[0]
    return "n90__" + base[len("sin90__"):] if base.startswith("sin90__") else base


def predecir(p: dict, X: pd.DataFrame) -> tuple[np.ndarray, pd.DataFrame]:
    """Probabilidad y contribuciones al logit por variable original (índice = X.index)."""
    D = _diseno(X, p["numericas"], p["niveles"])
    cols = p["columnas"]
    Z = ((D[cols] - pd.Series(p["media"])[cols]) / pd.Series(p["escala"])[cols]).fillna(0.0)
    contrib = Z * pd.Series(p["coef"])[cols]
    logit = p["intercepto"] + contrib.sum(axis=1)
    cal = p.get("calibracion") or {}
    if cal.get("metodo") == "platt":
        # logit calibrado = a·logit + b: los drivers se escalan por `a` para seguir sumando el logit final
        logit = cal["a"] * logit + cal["b"]
        contrib = contrib * cal["a"]
    agrupado = contrib.T.groupby(_grupo).sum().T
    return (1.0 / (1.0 + np.exp(-logit.values))), agrupado


# ---------------------------------------------------------------- calibración
TOLERANCIA_PP = 0.03     # error tolerado por banda: máx(3 puntos porcentuales, 2 errores estándar)
MIN_CAL = 10             # casos mínimos de cada clase para medir o ajustar la calibración
MIN_BANDA_CAL = 50       # con menos clientes por banda la medición no permite afirmar nada: "no evaluable"


def bandas_terciles(p: np.ndarray) -> np.ndarray:
    """Terciles por orden (empates resueltos por orden de llegada): tercio superior = alta."""
    if len(p) == 0:
        return np.array([], dtype=object)
    r = pd.Series(p).rank(method="first", ascending=False).values
    n = len(p)
    return np.where(r <= np.ceil(n / 3), "alta", np.where(r <= np.ceil(2 * n / 3), "media", "baja"))


def calibracion(y: pd.Series, p: np.ndarray) -> dict:
    """Probabilidad predicha promedio contra tasa observada, por banda. Cumple si en cada banda
    |predicho − observado| ≤ máx(TOLERANCIA_PP, 2·√(p(1−p)/n)), con p = probabilidad predicha promedio: 3 puntos es
    el error que importa para el negocio, y los 2 errores estándar (bajo la hipótesis "el modelo está calibrado")
    evitan reprobar por puro ruido cuando la banda es chica. Con menos de MIN_BANDA_CAL clientes en alguna banda,
    o menos de MIN_CAL casos de cada clase, la medición es "no evaluable"."""
    yv = y.values.astype(float)
    b = bandas_terciles(p)
    filas = []
    for k in ("alta", "media", "baja"):
        m = b == k
        n = int(m.sum())
        if not n:
            continue
        o, pr = float(yv[m].mean()), float(p[m].mean())
        tol = max(TOLERANCIA_PP, 2 * float(np.sqrt(max(pr * (1 - pr), 1e-9) / n)))
        filas.append({"banda": k, "n": n, "predicho": round(pr, 4), "observado": round(o, 4),
                      "error": round(pr - o, 4), "tolerancia": round(tol, 4), "cumple": bool(abs(pr - o) <= tol)})
    pos, neg = int(yv.sum()), int(len(yv) - yv.sum())
    return {"por_banda": filas, "error_max": round(max((abs(f["error"]) for f in filas), default=0.0), 4),
            "cumple": all(f["cumple"] for f in filas),
            "evaluable": pos >= MIN_CAL and neg >= MIN_CAL and all(f["n"] >= MIN_BANDA_CAL for f in filas)}


def ajustar_platt(y: pd.Series, p: np.ndarray) -> dict:
    """Platt: regresión logística de la etiqueta sobre el logit del modelo (dos parámetros, a y b)."""
    pc = np.clip(p, 1e-6, 1 - 1e-6)
    lg = np.log(pc / (1 - pc)).reshape(-1, 1)
    lr = LogisticRegression(C=1e6, max_iter=1000).fit(lg, y.values.astype(int))
    return {"metodo": "platt", "a": float(lr.coef_[0][0]), "b": float(lr.intercept_[0])}


def ajustar_calibrado(Xtr: pd.DataFrame, ytr: pd.Series, Xcal: pd.DataFrame, ycal: pd.Series) -> dict:
    """Ajusta el modelo con el entrenamiento y mide su calibración en los cortes de calibración (posteriores, que el
    modelo no vio). Platt se aplica solo si (1) el modelo crudo no cumple la tolerancia y (2) Platt mejora fuera de
    muestra: ajustado con los cortes de calibración más viejos, tiene que bajar el error en el más reciente. En
    los datos de prueba un Platt ajustado sin esa validación perseguía la variación de un mes y empeoraba."""
    p = ajustar(Xtr, ytr)
    if Xcal.empty:
        p["calibracion"] = {"metodo": "ninguna", "motivo": "sin_corte_de_calibracion"}
        return p
    crudo, _ = predecir(p, Xcal)
    med = calibracion(ycal, crudo)
    base = {"medicion_cruda": med}
    if not med["evaluable"]:
        p["calibracion"] = {"metodo": "ninguna", "motivo": "no_evaluable", **base}
        return p
    if med["cumple"]:
        p["calibracion"] = {"metodo": "ninguna", "motivo": "cumple_tolerancia", **base}
        return p
    corte = pd.Series([i.split("|", 1)[0] for i in Xcal.index], index=Xcal.index)
    ultimo = corte == corte.max()
    if ultimo.all() or (~ultimo).sum() == 0:
        p["calibracion"] = {"metodo": "ninguna", "motivo": "sin_validacion_posible", **base}
        return p
    viejo = ajustar_platt(ycal[~ultimo], crudo[~ultimo.values])
    lg = np.log(np.clip(crudo[ultimo.values], 1e-6, 1 - 1e-6) / (1 - np.clip(crudo[ultimo.values], 1e-6, 1 - 1e-6)))
    con_platt = 1 / (1 + np.exp(-(viejo["a"] * lg + viejo["b"])))
    err_crudo = calibracion(ycal[ultimo], crudo[ultimo.values])["error_max"]
    err_platt = calibracion(ycal[ultimo], con_platt)["error_max"]
    validacion = {"error_sin_platt": err_crudo, "error_con_platt": err_platt}
    if err_platt < err_crudo:
        p["calibracion"] = {**ajustar_platt(ycal, crudo), "motivo": "no_cumple_tolerancia",
                            "validacion": validacion, **base}
    else:
        p["calibracion"] = {"metodo": "ninguna", "motivo": "platt_no_mejora", "validacion": validacion, **base}
    return p


# ---------------------------------------------------------------- regla simple
_COMPONENTES = {
    # etapa -> [(variable, signo)]  signo +1: valor alto => más probable el evento modelado
    "retencion": [("dias_sin_eventos", +1), ("eventos_90d", -1), ("monto_90d", -1)],
    "activacion": [("eventos_90d", -1), ("dias_sin_eventos", +1), ("antiguedad_dias", +1)],
    "uso_valor": [("dias_sin_eventos", -1), ("eventos_90d", +1), ("monto_90d", +1)],
    "cobranza": [("pct_tarde_180d", +1), ("atraso_prom_180d", +1), ("impagas_vencidas", +1)],
}
_Q = np.linspace(0, 1, 101)


def ajustar_regla(etapa: str, X: pd.DataFrame) -> dict:
    """Recencia, frecuencia y monto (o atraso en cobranza). Percentiles de referencia de la población de entrenamiento."""
    comps = [(v, s) for v, s in _COMPONENTES[etapa] if v in X.columns]
    return {"tipo": "regla_rfm", "componentes": [[v, s] for v, s in comps],
            "cuantiles": {v: [float(q) for q in np.quantile(X[v].astype(float), _Q)] for v, _ in comps},
            "referencia": {v: float(X[v].mean()) for v, _ in comps}}


def _percentil(x: np.ndarray, cuantiles: list[float]) -> np.ndarray:
    q = np.asarray(cuantiles)
    # promedio de los dos lados para que los empates (muchos ceros) caigan en el medio del rango
    izq = np.searchsorted(q, x, side="left") / (len(q) - 1)
    der = (np.searchsorted(q, x, side="right") - 1) / (len(q) - 1)
    return np.clip((izq + np.maximum(der, izq)) / 2.0, 0, 1)


def predecir_regla(p: dict, X: pd.DataFrame) -> tuple[np.ndarray, pd.DataFrame]:
    partes = {}
    for v, s in p["componentes"]:
        pc = _percentil(X[v].astype(float).values, p["cuantiles"][v])
        partes[v] = (pc if s > 0 else 1 - pc) - 0.5
    contrib = pd.DataFrame(partes, index=X.index)
    return (contrib.mean(axis=1) + 0.5).clip(0, 1).values, contrib


# ---------------------------------------------------------------- drivers
def _ref(r):
    return r if r is None or isinstance(r, str) else round(float(r), 4)


def drivers(X: pd.DataFrame, contrib: pd.DataFrame, referencia: dict, n: int = N_DRIVERS) -> list[list[dict]]:
    """Por cliente, las `n` variables que más mueven su puntaje (con valor, referencia y efecto con signo)."""
    out = []
    vals = contrib.values
    cols = list(contrib.columns)
    orden = np.argsort(-np.abs(vals), axis=1)[:, :n]
    for i, idx in enumerate(X.index):
        fila = []
        for j in orden[i]:
            v = cols[j]
            ef = float(vals[i, j])
            if abs(ef) < 1e-6:
                continue
            crudo = X.at[idx, v]
            if not isinstance(crudo, str) and crudo is not None and pd.isna(crudo):
                crudo = None
            fila.append({"variable": v, "valor": crudo if isinstance(crudo, str) or crudo is None else round(float(crudo), 4),
                         "referencia": _ref(referencia.get(v)),
                         "efecto": round(ef, 4)})
        out.append(fila)
    return out
