"""Motor de puntaje: backtest temporal, entrenamiento, puntaje del día, bandas y drivers.

Línea de tiempo (R = último día con datos, H = horizonte de la etapa, paso = 30 días):

    entrenamiento del backtest       calibración                 evaluación     hoy
    c <= R-3H-60, cada 30 días       c = R-2H-60, R-2H-30, R-2H  c = R-H        R

   Cada corte necesita H días para conocer su etiqueta: la del entrenamiento se conoce antes del primer corte
   de calibración, la de calibración en R-H y la de evaluación recién en R. Nada de lo que se usa para ajustar
   mira la evaluación.

1. Backtest (entrenar en el pasado, evaluar en el futuro): el modelo se entrena con los cortes de entrenamiento,
   se mide su calibración en los cortes de calibración (si no cumple la tolerancia se ajusta Platt ahí) y se evalúa
   en R-H. Se reporta AUC, tasa del evento y probabilidad predicha contra observada por banda, y la comparación
   con la regla simple sobre el mismo período.
2. Modelo de producción: mismo procedimiento corrido H días más adelante (calibración en R-H-60, R-H-30 y R-H;
   entrenamiento antes) para usar la información más reciente. La calidad que se informa es la del backtest.
3. Puntaje del día: población puntuable a R, probabilidad (calibrada si correspondió), bandas por terciles y
   drivers.

Si los datos no alcanzan (sin historia para el backtest o pocos casos del evento), se usa la regla simple y queda
marcado `metodo = 'regla'` con el motivo.
"""
from __future__ import annotations
import json
from datetime import date, timedelta
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, brier_score_loss
from ..kpis import fecha_corte_por_defecto
from ..tenants import config_activa, auditar
from ..textos import t
from . import variables, etapas, modelo

PASO_DIAS = 30
MAX_CORTES = 12
HISTORIA_MIN_DIAS = 90          # un corte necesita al menos 90 días de historia previa (ventana de variables)
MIN_POSITIVOS = 30             # clientes distintos con el evento en los cortes de entrenamiento
MIN_NEGATIVOS = 30             # clientes distintos sin el evento
MIN_FILAS = 200                # clientes distintos en total
MIN_EVAL = 10                   # casos mínimos de cada clase en la evaluación para informar AUC
REENTRENAR_DIAS = 30            # el proceso nocturno reentrena si el modelo vigente tiene más de esto
CORTES_CALIBRACION = 3          # la calibración se mide (y Platt se ajusta) con 3 cortes mensuales juntos: con uno solo
                                # se persigue la variación de un mes (en cobranza la tasa fluctúa ±3 puntos)


def _no_predictivos(cfg) -> tuple[str, ...]:
    return (cfg.politica_contacto.atributo_consentimiento,)


def huella_config(cfg, etapa: str) -> str:
    """Huella de la parte de la configuración que cambia el modelo de una etapa (su regla, horizonte, gracia de
    cobranza y atributos excluidos). Cambiar el valor económico, el catálogo o la política de contacto crea otra
    versión de configuración pero no obliga a reentrenar."""
    import hashlib
    parte = {"etapa": cfg.etapas[etapa].model_dump(), "gracia": cfg.cobranza.gracia_dias,
             "no_predictivos": list(_no_predictivos(cfg))}
    return hashlib.sha256(json.dumps(parte, sort_keys=True).encode()).hexdigest()[:16]


def _cortes(desde: date, minimo: date, n: int = MAX_CORTES) -> list[date]:
    return [c for c in (desde - timedelta(days=PASO_DIAS * k) for k in range(n)) if c >= minimo]


def _linea_de_tiempo(c_cal: date, h: int, minimo: date) -> tuple[list[date], list[date]]:
    """(cortes de calibración, cortes de entrenamiento) para un último corte de calibración `c_cal`."""
    cal = _cortes(c_cal, minimo, CORTES_CALIBRACION)
    primero = c_cal - timedelta(days=PASO_DIAS * (CORTES_CALIBRACION - 1))
    return cal, _cortes(primero - timedelta(days=h), minimo)


def _inicio_datos(conn) -> date | None:
    r = conn.execute("""SELECT LEAST((SELECT min(fecha_alta) FROM entidades WHERE tipo='persona'),
                                    (SELECT (min(ts) AT TIME ZONE %s)::date FROM eventos)) AS d""",
                     (variables.zona(conn),)).fetchone()
    return r["d"]


def _armar(conn, etapa, cfg, spec, cortes, h) -> tuple[pd.DataFrame, pd.Series, list[dict]]:
    Xs, ys, resumen = [], [], []
    for c in cortes:
        pob = etapas.poblacion(conn, etapa, cfg, c)
        if pob.empty:
            continue
        y = etapas.etiqueta(conn, etapa, cfg, spec, pob, c, h).dropna()
        if y.empty:
            continue
        X = variables.calcular(conn, c, spec, pob.loc[y.index])
        X.index = [f"{c}|{i}" for i in X.index]
        y.index = X.index
        Xs.append(X)
        ys.append(y)
        resumen.append({"corte": str(c), "filas": int(len(y)), "positivos": int(y.sum())})
    if not Xs:
        return pd.DataFrame(), pd.Series(dtype=float), resumen
    return pd.concat(Xs), pd.concat(ys), resumen


_bandas = modelo.bandas_terciles


def _metricas(y: pd.Series, p: np.ndarray, probabilidad: bool = True) -> dict:
    """`probabilidad=False` para la regla simple: su puntaje es un percentil, no una probabilidad."""
    pos, neg = int(y.sum()), int(len(y) - y.sum())
    out = {"n": int(len(y)), "positivos": pos, "tasa_base": round(pos / len(y), 4) if len(y) else None}
    b = _bandas(p)
    out["por_banda"] = [{"banda": k, "n": int((b == k).sum()),
                         "tasa": round(float(y.values[b == k].mean()), 4) if (b == k).any() else None}
                        for k in ("alta", "media", "baja")]
    if pos >= MIN_EVAL and neg >= MIN_EVAL:
        out["auc"] = round(float(roc_auc_score(y.values, p)), 4)
        out["brier"] = round(float(brier_score_loss(y.values, np.clip(p, 0, 1))), 4)
        alta = out["por_banda"][0]["tasa"]
        out["lift_banda_alta"] = round(alta / out["tasa_base"], 2) if out["tasa_base"] else None
    else:
        out["auc"] = None
    if probabilidad:
        out["calibracion"] = modelo.calibracion(y, p)
    return out


def _suficiente(y: pd.Series) -> str | None:
    """Cuenta clientes distintos, no filas: el mismo cliente aparece en varios cortes y apilar cortes no agrega
    información nueva sobre él (con 60 clientes y 12 cortes habría 700 filas)."""
    ent = pd.Series([i.split("|", 1)[1] for i in y.index], index=y.index)
    n = ent.nunique()
    pos = ent[y == 1].nunique()
    neg = ent[y == 0].nunique()
    if n < MIN_FILAS or pos < MIN_POSITIVOS or neg < MIN_NEGATIVOS:
        return t("calidad.pocos_eventos", pos=pos, neg=neg, n=n)
    return None


def entrenar(conn, etapa: str, fecha: date | None = None, actor: str = "sistema") -> dict:
    """Backtest + entrenamiento. Crea una versión nueva (inmutable) en `modelos` y la devuelve."""
    version_cfg, cfg = config_activa(conn)
    R = fecha or fecha_corte_por_defecto(conn)
    spec = variables.descubrir(conn, cfg.cobranza.gracia_dias, _no_predictivos(cfg))
    motivo = etapas.puntuable(etapa, cfg, spec)
    if motivo:
        raise ValueError(f"No se puede puntuar '{etapa}': {motivo}")
    h = etapas.horizonte(etapa, cfg)
    ini = _inicio_datos(conn)
    minimo = ini + timedelta(days=HISTORIA_MIN_DIAS)
    c_eval = R - timedelta(days=h)
    cal_bt, cortes_bt = _linea_de_tiempo(c_eval - timedelta(days=h), h, minimo)     # backtest
    cal_prod, cortes_prod = _linea_de_tiempo(c_eval, h, minimo)                     # producción

    avisos, metodo, motivo_metodo = [], "modelo", None
    calidad: dict = {"horizonte_dias": h}
    if not cortes_bt or len(cal_bt) < CORTES_CALIBRACION:
        metodo, motivo_metodo = "regla", t("calidad.sin_historia")
    else:
        Xtr, ytr, res_tr = _armar(conn, etapa, cfg, spec, cortes_bt, h)
        Xca, yca, res_ca = _armar(conn, etapa, cfg, spec, cal_bt, h)
        Xev, yev, res_ev = _armar(conn, etapa, cfg, spec, [c_eval], h)
        calidad["backtest"] = {"cortes_entrenamiento": res_tr, "cortes_calibracion": res_ca, "corte_evaluacion": res_ev}
        motivo_metodo = _suficiente(ytr) if len(ytr) else t("calidad.sin_historia")
        if motivo_metodo or yev.empty:
            metodo = "regla"
            motivo_metodo = motivo_metodo or t("calidad.sin_historia")
        else:
            p_bt = modelo.ajustar_calibrado(Xtr, ytr, Xca, yca)
            prob, _ = modelo.predecir(p_bt, Xev)
            regla_bt = modelo.ajustar_regla(etapa, Xtr)
            prob_r, _ = modelo.predecir_regla(regla_bt, Xev)
            calidad["backtest"]["modelo"] = _metricas(yev, prob)
            calidad["backtest"]["modelo"]["calibracion_aplicada"] = {
                k: v for k, v in p_bt["calibracion"].items() if k != "medicion_cruda"}
            if p_bt["calibracion"]["metodo"] != "ninguna":     # para comparar: la misma evaluación sin calibrar
                crudo, _ = modelo.predecir({**p_bt, "calibracion": None}, Xev)
                calidad["backtest"]["modelo"]["calibracion_sin_ajuste"] = modelo.calibracion(yev, crudo)
            calidad["backtest"]["regla"] = _metricas(yev, prob_r, probabilidad=False)
            cal = calidad["backtest"]["modelo"]["calibracion"]
            if not cal["evaluable"]:
                avisos.append(t("calidad.calibracion_no_evaluable"))
            elif not cal["cumple"]:
                avisos.append(t("calidad.mal_calibrado", error=round(100 * cal["error_max"], 1)))
            auc_m, auc_r = calidad["backtest"]["modelo"]["auc"], calidad["backtest"]["regla"]["auc"]
            if auc_m is None:
                avisos.append(t("calidad.no_evaluable"))
            elif auc_r is not None and auc_m < auc_r:
                avisos.append(t("calidad.modelo_peor_que_regla"))

    if metodo == "modelo":
        Xp, yp, res_p = _armar(conn, etapa, cfg, spec, cortes_prod, h)
        Xpc, ypc, res_pc = _armar(conn, etapa, cfg, spec, cal_prod, h)
        params = modelo.ajustar_calibrado(Xp, yp, Xpc, ypc)
        entrenamiento = {"cortes": res_p, "filas": int(len(yp)), "positivos": int(yp.sum()),
                         "cortes_calibracion": res_pc, "calibracion": params["calibracion"]["metodo"]}
    else:
        # La regla no necesita etiqueta: se calibra (percentiles) sobre la población de hoy.
        X_hoy = variables.calcular(conn, R, spec, etapas.poblacion(conn, etapa, cfg, R))
        if X_hoy.empty:
            raise ValueError(f"No hay clientes puntuables en '{etapa}' a {R}")
        params = modelo.ajustar_regla(etapa, X_hoy)
        entrenamiento = {"cortes": [], "filas": int(len(X_hoy)), "positivos": None}
    calidad["avisos"] = avisos

    num = conn.execute("SELECT COALESCE(max(numero), 0) + 1 AS n FROM modelos WHERE etapa = %s", (etapa,)).fetchone()["n"]
    version = f"{etapa}-v{num}"
    definicion = {"etapa": etapa, "evento_modelado": etapas.evento_modelado(etapa, spec), "horizonte_dias": h,
                  "variables": spec, "version_variables": variables.VERSION_VARIABLES,
                  "huella_config": huella_config(cfg, etapa),
                  "entrenamiento": entrenamiento, "paso_dias": PASO_DIAS}
    demo = conn.execute("SELECT is_demo FROM tenants").fetchone()["is_demo"]
    conn.execute("""INSERT INTO modelos (tenant_id, version, etapa, numero, metodo, motivo_metodo, fecha_referencia,
                                         config_version, definicion, parametros, calidad, is_demo, creado_por)
                    VALUES (app_tenant(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                 (version, etapa, num, metodo, motivo_metodo, R, version_cfg, json.dumps(definicion, default=str),
                  json.dumps(params), json.dumps(calidad, default=str), demo, actor))
    auditar(conn, actor, "modelo_entrenado", {"version": version, "metodo": metodo, "fecha_referencia": str(R)})
    return obtener_modelo(conn, version)


def obtener_modelo(conn, version: str) -> dict:
    return conn.execute("SELECT * FROM modelos WHERE version = %s", (version,)).fetchone()


def modelo_vigente(conn, etapa: str) -> dict | None:
    return conn.execute("SELECT * FROM modelos WHERE etapa = %s ORDER BY numero DESC LIMIT 1", (etapa,)).fetchone()


def vigente_para_variables(m: dict) -> bool:
    return m["definicion"].get("version_variables") == variables.VERSION_VARIABLES


def puntuar(conn, etapa: str, fecha: date | None = None, modelo_version: str | None = None, actor: str = "sistema") -> dict:
    """Calcula y guarda los puntajes de la etapa a la fecha (reemplaza los de esa misma fecha y etapa)."""
    m = obtener_modelo(conn, modelo_version) if modelo_version else modelo_vigente(conn, etapa)
    if not m:
        raise ValueError(f"No hay modelo entrenado para '{etapa}'")
    if not vigente_para_variables(m):
        raise ValueError(f"El modelo {m['version']} se entrenó con otra versión del cálculo de variables: reentrenar")
    version_cfg, cfg = config_activa(conn)
    R = fecha or fecha_corte_por_defecto(conn)
    spec = m["definicion"]["variables"]
    pob = etapas.poblacion(conn, etapa, cfg, R)
    X = variables.calcular(conn, R, spec, pob)
    if X.empty:
        return {"etapa": etapa, "fecha": str(R), "puntuados": 0, "modelo_version": m["version"]}
    p = m["parametros"]
    prob, contrib = (modelo.predecir(p, X) if m["metodo"] == "modelo" else modelo.predecir_regla(p, X))
    bandas = _bandas(prob)
    drv = modelo.drivers(X, contrib, p["referencia"])
    demo = conn.execute("SELECT is_demo FROM tenants").fetchone()["is_demo"]
    conn.execute("DELETE FROM puntajes WHERE etapa = %s AND fecha = %s", (etapa, R))
    # COPY no está permitido con RLS: insert con unnest (una sola sentencia, respeta la política del tenant).
    conn.execute("""INSERT INTO puntajes (tenant_id, entidad_id, etapa, fecha, puntaje, banda, drivers, metodo,
                                          modelo_version, config_version, is_demo)
                    SELECT app_tenant(), u.eid, %(etapa)s, %(f)s, u.p, u.b, u.d::jsonb, %(met)s, %(ver)s, %(cv)s, %(demo)s
                    FROM unnest(%(eids)s::uuid[], %(ps)s::numeric[], %(bs)s::text[], %(ds)s::text[]) AS u(eid, p, b, d)""",
                 {"etapa": etapa, "f": R, "met": m["metodo"], "ver": m["version"], "cv": version_cfg, "demo": demo,
                  "eids": list(X.index), "ps": [round(float(x), 5) for x in prob], "bs": list(bandas),
                  "ds": [json.dumps(d, default=str) for d in drv]})
    auditar(conn, actor, "puntajes_calculados", {"etapa": etapa, "fecha": str(R), "modelo_version": m["version"],
                                                 "puntuados": int(len(X))})
    cuenta = {k: int((bandas == k).sum()) for k in ("alta", "media", "baja")}
    return {"etapa": etapa, "fecha": str(R), "puntuados": int(len(X)), "bandas": cuenta,
            "modelo_version": m["version"], "metodo": m["metodo"], "config_version": version_cfg}


def proceso_nocturno(conn, fecha: date | None = None, forzar_entrenamiento: bool = False, actor: str = "proceso_nocturno") -> list[dict]:
    """Para cada etapa puntuable: reentrena si no hay modelo, si cambió la parte de la configuración que afecta al
    modelo de esa etapa (`huella_config`), si cambió el cálculo de variables o si el modelo tiene más de
    REENTRENAR_DIAS; después puntúa. Los puntajes guardan siempre la versión de configuración vigente. Pensado para correr una vez por día por tenant."""
    version_cfg, cfg = config_activa(conn)
    R = fecha or fecha_corte_por_defecto(conn)
    spec = variables.descubrir(conn, cfg.cobranza.gracia_dias, _no_predictivos(cfg))
    salida = []
    for etapa in etapas.PUNTUABLES:
        motivo = etapas.puntuable(etapa, cfg, spec)
        if motivo:
            salida.append({"etapa": etapa, "omitida": motivo})
            continue
        m = modelo_vigente(conn, etapa)
        entreno = (forzar_entrenamiento or m is None
                   or m["definicion"].get("huella_config") != huella_config(cfg, etapa)
                   or not vigente_para_variables(m) or (R - m["fecha_referencia"]).days > REENTRENAR_DIAS)
        try:
            if entreno:
                m = entrenar(conn, etapa, R, actor)
            r = puntuar(conn, etapa, R, m["version"], actor)
        except ValueError as e:      # p. ej. nadie en la población hoy: la etapa se omite, las demás siguen
            conn.rollback()
            salida.append({"etapa": etapa, "omitida": str(e)})
            continue
        r["entrenado"] = bool(entreno)
        conn.commit()
        salida.append(r)
    return salida


def lista_clientes(conn, etapa: str, banda: str | None = None, limite: int = 50, fecha: date | None = None) -> list[dict]:
    """Clientes con puntaje, banda y drivers (último cálculo de la etapa, o el de `fecha`)."""
    if fecha is None:
        r = conn.execute("SELECT max(fecha) AS f FROM puntajes WHERE etapa = %s", (etapa,)).fetchone()
        fecha = r["f"]
        if fecha is None:
            return []
    rows = conn.execute(
        """SELECT e.id_externo, e.atributos, p.puntaje, p.banda, p.drivers, p.metodo, p.modelo_version,
                  p.config_version, p.fecha, emp.id_externo AS empresa
           FROM puntajes p JOIN entidades e ON e.id = p.entidad_id
           LEFT JOIN entidades emp ON emp.id = e.parent_id
           WHERE p.etapa = %(etapa)s AND p.fecha = %(f)s AND (%(banda)s::text IS NULL OR p.banda = %(banda)s)
           ORDER BY p.puntaje DESC, e.id_externo LIMIT %(lim)s""",
        {"etapa": etapa, "f": fecha, "banda": banda, "lim": limite}).fetchall()
    return [dict(r) for r in rows]
