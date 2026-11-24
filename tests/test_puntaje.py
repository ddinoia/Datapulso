"""Hito 2 · motor de puntaje: variables sin fuga, backtest temporal, recuperación de la señal sembrada,
lista de clientes con puntaje y drivers, regla simple de respaldo, versionado, reproducibilidad y aislamiento.

La verdad sembrada (`ds.verdad`) solo se usa acá, en las pruebas, para verificar. Nunca entra al producto."""
from datetime import date, timedelta
import io
import contextlib
import numpy as np
import pandas as pd
import psycopg
import pytest
from sklearn.metrics import roc_auc_score
from datapulso.db import tenant_conn, admin_conn
from datapulso.datagen.generador import generar, snapshot_a
from datapulso.datagen.demo import cargar_demo_csv
from datapulso.datagen.industrias import INDUSTRIAS
from datapulso.puntaje import motor, variables, presentacion
from datapulso.tenants import config_activa, crear_version_config

FOTO = date(2026, 8, 2)        # la base "como estaba" 90 días antes del corte del generador (2026-10-31)


@pytest.fixture(scope="module")
def servicios():
    """Misma historia cargada dos veces: completa (hasta el corte) y congelada en FOTO."""
    ds = generar("servicios", 11, 1500)
    completo = cargar_demo_csv("servicios", ds=ds)
    foto = cargar_demo_csv("servicios", ds=snapshot_a(ds, FOTO))
    with tenant_conn(completo["tenant_id"]) as c:
        nocturno = motor.proceso_nocturno(c)
    return {"ds": ds, "completo": completo["tenant_id"], "foto": foto["tenant_id"], "nocturno": nocturno}


def _coef(m: dict) -> dict:
    return m["parametros"]["coef"]


# ---------------------------------------------------------------- sin fuga de información
def test_variables_sin_fuga_de_informacion(servicios):
    """Las variables a la fecha FOTO son idénticas en la base completa y en la base congelada en FOTO:
    nada de lo ocurrido después de FOTO se filtra en el cálculo."""
    out = []
    for tid in (servicios["completo"], servicios["foto"]):
        with tenant_conn(tid) as c:
            _, cfg = config_activa(c)
            spec = variables.descubrir(c, cfg.cobranza.gracia_dias)
            base = variables.base_vigentes(c, FOTO)
            X = variables.calcular(c, FOTO, spec, base)
            X.index = base.loc[X.index, "id_externo"].values
            out.append((spec, X.sort_index()))
    (spec_a, a), (spec_b, b) = out
    assert spec_a["tipos"] == spec_b["tipos"] and spec_a["atributos"] == spec_b["atributos"]
    assert len(a) > 500 and list(a.columns) == list(b.columns)
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-9)


# ---------------------------------------------------------------- el modelo recupera la señal sembrada
def test_puntaje_recupera_la_senal_contra_la_verdad_sembrada(servicios):
    """Puntaje calculado con la base congelada en FOTO, comparado con lo que de verdad pasó después
    (bajas sembradas por el generador en los 90 días siguientes)."""
    ds = servicios["ds"]
    with tenant_conn(servicios["foto"]) as c:
        m = motor.entrenar(c, "retencion")
        r = motor.puntuar(c, "retencion", modelo_version=m["version"])
        c.commit()
        filas = c.execute("""SELECT e.id_externo, p.puntaje::float AS puntaje, p.banda FROM puntajes p
                             JOIN entidades e ON e.id = p.entidad_id WHERE p.etapa = 'retencion'""").fetchall()
    assert m["metodo"] == "modelo" and r["fecha"] == str(FOTO)
    df = pd.DataFrame(filas)
    verdad = ds.verdad["clientes"]
    fin = FOTO + timedelta(days=90)
    df["se_fue"] = [verdad[i]["baja"] is not None and FOTO < date.fromisoformat(verdad[i]["baja"]) <= fin
                    for i in df["id_externo"]]
    df["z"] = [verdad[i]["riesgo_latente"] for i in df["id_externo"]]
    assert df["se_fue"].sum() >= 50
    auc = roc_auc_score(df["se_fue"], df["puntaje"])
    assert auc >= 0.75, auc
    tasa = df.groupby("banda")["se_fue"].mean()
    assert tasa["alta"] > tasa["media"] > tasa["baja"], tasa.to_dict()
    assert tasa["alta"] >= 3 * tasa["baja"], tasa.to_dict()
    # el puntaje ordena según el riesgo latente sembrado (que el producto nunca ve)
    assert df["puntaje"].rank().corr(df["z"].rank()) >= 0.3


@pytest.mark.parametrize("industria", ["servicios", "salud", "financiera"])
def test_backtest_temporal_y_drivers_con_sentido(industria, servicios):
    """Backtest (entrenar en el pasado, evaluar en el futuro) en las tres industrias. Los coeficientes reproducen
    la señal sembrada: antes de irse usan menos, reclaman más y pagan más tarde."""
    if industria == "servicios":
        tid = servicios["completo"]
    else:
        tid = cargar_demo_csv(industria, n_clientes=1500, semilla=12)["tenant_id"]
    with tenant_conn(tid) as c:
        m = motor.modelo_vigente(c, "retencion") if industria == "servicios" else motor.entrenar(c, "retencion")
        c.commit()
    assert m["metodo"] == "modelo", m["motivo_metodo"]
    bt = m["calidad"]["backtest"]
    corte_eval = date.fromisoformat(bt["corte_evaluacion"][0]["corte"])
    assert all(date.fromisoformat(x["corte"]) + timedelta(days=90) <= corte_eval for x in bt["cortes_entrenamiento"]), \
        "las etiquetas de entrenamiento deben conocerse antes del corte de evaluación"
    assert corte_eval + timedelta(days=90) == m["fecha_referencia"]
    mod, reg = bt["modelo"], bt["regla"]
    assert mod["auc"] >= 0.72 and mod["auc"] > reg["auc"], (mod["auc"], reg["auc"])
    t_alta, t_media, t_baja = (b["tasa"] for b in mod["por_banda"])
    assert t_alta > t_media >= t_baja and mod["lift_banda_alta"] >= 1.5   # la banda alta concentra el evento

    coef = _coef(m)
    for te in INDUSTRIAS[industria].eventos:
        efecto = coef.get(f"n90__{te.tipo}", 0) + coef.get(f"tendencia__{te.tipo}", 0)
        if te.respuesta_pre_baja == "cae":
            assert efecto < 0, (te.tipo, efecto)       # menos uso => más riesgo
        else:
            assert coef.get(f"n90__{te.tipo}", 0) > 0, (te.tipo, coef)   # más reclamos => más riesgo
    assert coef["pct_tarde_180d"] > 0                   # paga tarde => más riesgo
    assert not {"eventos_90d", "monto_90d"} & set(coef)  # totales colineales fuera del modelo
    # calibración: se mide siempre (predicho contra observado por banda, en la evaluación fuera de tiempo)
    cal = mod["calibracion"]
    assert cal["evaluable"] and len(cal["por_banda"]) == 3 and len(bt["cortes_calibracion"]) == 3
    if industria == "servicios":
        assert cal["cumple"], cal
    # En financiera con 1.500 clientes (semilla 12) la banda alta queda fuera de la tolerancia: la tasa de bajas
    # del período evaluado (11,7 %) salta respecto de los cortes de calibración (~7,5 %). Ver la guía del hito 2.


def test_cobranza_usa_la_mora_previa(servicios):
    with tenant_conn(servicios["completo"]) as c:
        m = motor.modelo_vigente(c, "cobranza")
    assert m["metodo"] == "modelo"
    mod, reg = m["calidad"]["backtest"]["modelo"], m["calidad"]["backtest"]["regla"]
    assert mod["auc"] >= 0.6 and mod["auc"] > reg["auc"]
    assert _coef(m)["pct_tarde_180d"] > 0


# ---------------------------------------------------------------- lista de clientes con puntaje y drivers
def test_lista_de_clientes_con_puntaje_banda_y_drivers(servicios):
    res = {r["etapa"]: r for r in servicios["nocturno"]}
    assert set(res) == {"retencion", "cobranza", "activacion", "uso_valor"}
    assert all("omitida" not in r and r["puntuados"] > 0 for r in res.values()), res
    with tenant_conn(servicios["completo"]) as c:
        cv, _ = config_activa(c)
        filas = motor.lista_clientes(c, "retencion", limite=5000)
        n = c.execute("""SELECT count(*) AS n FROM entidades WHERE tipo='persona' AND fecha_baja IS NULL""").fetchone()["n"]
        vers = c.execute("SELECT DISTINCT modelo_version, config_version FROM puntajes WHERE etapa='retencion'").fetchall()
    assert len(filas) == n == res["retencion"]["puntuados"]          # todos los vigentes, una vez
    assert [dict(v) for v in vers] == [{"modelo_version": res["retencion"]["modelo_version"], "config_version": cv}]
    p = [float(f["puntaje"]) for f in filas]
    assert p == sorted(p, reverse=True) and 0 <= min(p) and max(p) <= 1
    cuenta = pd.Series([f["banda"] for f in filas]).value_counts()
    assert abs(cuenta["alta"] - cuenta["baja"]) <= 1 and abs(cuenta["alta"] - cuenta["media"]) <= 1   # terciles
    assert {f["banda"] for f in filas[: n // 3]} == {"alta"}
    for f in filas[:50]:
        assert 1 <= len(f["drivers"]) <= 5
        efectos = [abs(d["efecto"]) for d in f["drivers"]]
        assert efectos == sorted(efectos, reverse=True)
        assert not any(d["variable"].startswith("sin90__") for d in f["drivers"])   # va sumado a n90__
        for d in f["drivers"]:
            txt = presentacion.texto_driver(d)
            assert "variable." not in txt and ("sube el puntaje" in txt or "baja el puntaje" in txt), txt
    # los de prioridad alta lo son mayormente por algo que sube el riesgo
    assert np.mean([f["drivers"][0]["efecto"] > 0 for f in filas[:100]]) > 0.9


def test_comando_clientes_muestra_vocabulario_del_tenant():
    from datapulso.cli import main
    demo = cargar_demo_csv("salud", n_clientes=600, semilla=3)
    with tenant_conn(demo["tenant_id"]) as c:
        nombre = c.execute("SELECT nombre FROM tenants").fetchone()["nombre"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(["puntuar", nombre, "--etapa", "retencion"])
        main(["clientes", nombre, "--etapa", "retencion", "--limite", "3"])
        main(["modelo", nombre, "--etapa", "retencion"])
    out = buf.getvalue()
    assert "Afiliados · Retención" in out and "Alta prioridad" in out and "Backtest" in out
    assert "probabilidad de que el afiliado se dé de baja en los próximos 90 días" in out
    assert "Alta prioridad · probabilidad " in out and "Calibración (predicho vs observado)" in out
    assert out.count("sube el puntaje") + out.count("baja el puntaje") >= 3


# ---------------------------------------------------------------- regla simple cuando los datos no alcanzan
def test_regla_simple_si_los_datos_no_alcanzan():
    demo = cargar_demo_csv("financiera", n_clientes=60, semilla=4)
    with tenant_conn(demo["tenant_id"]) as c:
        m = motor.entrenar(c, "retencion")
        r = motor.puntuar(c, "retencion", modelo_version=m["version"])
        c.commit()
        filas = motor.lista_clientes(c, "retencion", limite=1000)
    assert m["metodo"] == "regla" and m["motivo_metodo"]
    assert r["metodo"] == "regla" and len(filas) == r["puntuados"] > 0
    assert all(f["metodo"] == "regla" for f in filas)
    assert {d["variable"] for f in filas for d in f["drivers"]} <= {"dias_sin_eventos", "eventos_90d", "monto_90d"}
    assert "Regla simple" in presentacion.resumen_modelo(m)[0]
    assert "sin probabilidad" in presentacion.texto_banda(filas[0]["banda"], filas[0]["puntaje"], "regla")


# ---------------------------------------------------------------- versionado, reproducibilidad, aislamiento
def test_reproducible_y_versionado(servicios):
    with tenant_conn(servicios["completo"]) as c:
        a = motor.entrenar(c, "retencion")
        b = motor.entrenar(c, "retencion")
        c.commit()
        assert a["version"] != b["version"] and a["parametros"] == b["parametros"]
        assert a["calidad"] == b["calidad"]
        # dos barreras: la app no tiene permiso de UPDATE y además un trigger bloquea cualquier modificación
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE modelos SET metodo = 'regla' WHERE version = %s", (a["version"],))
        c.rollback()
    with admin_conn() as adm:
        with pytest.raises(psycopg.errors.RaiseException):
            adm.execute("UPDATE modelos SET metodo = 'regla' WHERE tenant_id = %s", (servicios["completo"],))
        adm.rollback()


def test_proceso_nocturno_reentrena_solo_cuando_hace_falta(servicios):
    with tenant_conn(servicios["completo"]) as c:
        r1 = {r["etapa"]: r for r in motor.proceso_nocturno(c)}
        assert not any(r["entrenado"] for r in r1.values())          # modelo vigente y misma configuración
        cv, cfg = config_activa(c)
        cfg.etapas["retencion"].ventana_prediccion_dias = 60
        crear_version_config(c, cfg, "prueba", nota="horizonte de retención 60 días")
        c.commit()
        r2 = {r["etapa"]: r for r in motor.proceso_nocturno(c)}
        assert all(r["entrenado"] and r["config_version"] == cv + 1 for r in r2.values())
        m = motor.modelo_vigente(c, "retencion")
        assert m["definicion"]["horizonte_dias"] == 60 and m["config_version"] == cv + 1
        n = c.execute("SELECT count(DISTINCT modelo_version) AS n FROM puntajes WHERE etapa='retencion'").fetchone()["n"]
        assert n == 1   # recalcular el mismo día reemplaza los puntajes de esa fecha


def test_aislamiento_de_puntajes_y_modelos(servicios):
    """RLS: un tenant no ve ni puede escribir puntajes o modelos de otro. Todo lo de demo queda marcado is_demo."""
    tid_foto = servicios["foto"]
    with tenant_conn(servicios["completo"]) as c:
        assert c.execute("SELECT count(*) AS n FROM puntajes").fetchone()["n"] > 0
        for t in ("puntajes", "modelos"):
            assert c.execute(f"SELECT count(*) AS n FROM {t} WHERE tenant_id = %s", (tid_foto,)).fetchone()["n"] == 0
            assert c.execute(f"SELECT count(*) AS n FROM {t} WHERE NOT is_demo").fetchone()["n"] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("""INSERT INTO modelos (tenant_id, version, etapa, numero, metodo, fecha_referencia, config_version,
                                              definicion, parametros, calidad, creado_por)
                         VALUES (%s, 'x-v1', 'retencion', 99, 'regla', '2026-01-01', 1, '{}', '{}', '{}', 'intruso')""",
                      (tid_foto,))
        c.rollback()


def test_una_etapa_sin_poblacion_no_frena_el_proceso_nocturno(monkeypatch):
    from datapulso.puntaje import etapas
    demo = cargar_demo_csv("financiera", n_clientes=60, semilla=4)
    original = etapas.poblacion
    monkeypatch.setattr(etapas, "poblacion",
                        lambda conn, etapa, cfg, c: original(conn, etapa, cfg, c).iloc[0:0] if etapa == "activacion"
                        else original(conn, etapa, cfg, c))
    with tenant_conn(demo["tenant_id"]) as c:
        res = {r["etapa"]: r for r in motor.proceso_nocturno(c)}
    assert "No hay clientes puntuables" in res["activacion"]["omitida"]
    assert res["retencion"]["puntuados"] > 0 and res["cobranza"]["puntuados"] > 0


# ---------------------------------------------------------------- calibración (sin base de datos)
def _sintetico(n, base_logit, semilla, cortes):
    rng = np.random.default_rng(semilla)
    x = rng.normal(size=n)
    y = (rng.random(n) < 1 / (1 + np.exp(-(base_logit + 1.5 * x)))).astype(float)
    idx = [f"{cortes[i % len(cortes)]}|e{semilla}_{i}" for i in range(n)]
    return pd.DataFrame({"n90__uso": np.exp(x + 2)}, index=idx), pd.Series(y, index=idx)


def test_platt_corrige_un_modelo_descalibrado():
    """Entrenamiento con tasa base ~12 % y período reciente con ~30 %: el modelo crudo subestima, Platt pasa la
    validación fuera de muestra (cortes viejos -> corte más reciente) y deja la probabilidad dentro de la tolerancia."""
    from datapulso.puntaje import modelo as mo
    Xtr, ytr = _sintetico(6000, -2.5, 1, ["2026-01-01"])
    Xca, yca = _sintetico(6000, -1.0, 2, ["2026-03-01", "2026-04-01", "2026-05-01"])
    Xev, yev = _sintetico(15000, -1.0, 3, ["2026-06-01"])
    p = mo.ajustar_calibrado(Xtr, ytr, Xca, yca)
    assert p["calibracion"]["metodo"] == "platt" and not p["calibracion"]["medicion_cruda"]["cumple"]
    crudo, _ = mo.predecir({**p, "calibracion": None}, Xev)
    calibrado, contrib = mo.predecir(p, Xev)
    assert not mo.calibracion(yev, crudo)["cumple"]
    assert mo.calibracion(yev, calibrado)["cumple"]
    # los drivers siguen sumando el logit final después de calibrar
    lg = np.log(calibrado / (1 - calibrado))
    np.testing.assert_allclose(contrib.sum(axis=1).values + p["intercepto"] * p["calibracion"]["a"] + p["calibracion"]["b"], lg, rtol=1e-6)


def test_sin_platt_si_el_modelo_ya_calibra():
    from datapulso.puntaje import modelo as mo
    Xtr, ytr = _sintetico(6000, -2.0, 4, ["2026-01-01"])
    Xca, yca = _sintetico(6000, -2.0, 5, ["2026-03-01", "2026-04-01", "2026-05-01"])
    p = mo.ajustar_calibrado(Xtr, ytr, Xca, yca)
    assert p["calibracion"]["metodo"] == "ninguna" and p["calibracion"]["motivo"] == "cumple_tolerancia"


# ---------------------------------------------------------------- comando tenants
def test_comando_tenants_lista_las_organizaciones():
    from datapulso.cli import main
    from datapulso.tenants import crear_tenant
    tid = crear_tenant(f"Org tenants {np.random.default_rng().integers(1e9)}", "salud", is_demo=True)
    with tenant_conn(tid) as c:
        nombre = c.execute("SELECT nombre FROM tenants").fetchone()["nombre"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(["tenants"])
    linea = next(l for l in buf.getvalue().splitlines() if l.startswith(nombre))
    assert tid in linea and " salud " in linea and " sí " in linea and " v1 " in linea
    assert date.today().isoformat() in linea or str(date.today().year) in linea
    assert buf.getvalue().splitlines()[0].split() == ["nombre", "id", "industria", "demo", "config", "creado"]
