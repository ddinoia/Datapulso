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
    agrupado = contrib.T.groupby(_grupo).sum().T
    return (1.0 / (1.0 + np.exp(-logit.values))), agrupado


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
