"""KPIs/funnel calculados desde la base contra un cálculo independiente en pandas; calidad del generador."""
from datetime import date, timedelta
import hashlib
import numpy as np
import pandas as pd
import pytest
from datapulso.db import tenant_conn
from datapulso import kpis
from datapulso.datagen.generador import generar
from datapulso.datagen.demo import cargar_demo_csv


def _pandas_funnel(ds, cfg):
    ent, ev, pg = ds.entidades, ds.eventos, ds.pagos
    corte = max(ev["ts"].dt.date.max(), max(pg["fecha_emision"]), max(ent["fecha_alta"]))
    per = ent[ent["tipo"] == "persona"].copy()
    ev = ev.assign(dia=ev["ts"].dt.date)
    out = {}
    for etapa in ("activacion", "uso_valor", "retencion"):
        r = cfg.etapas[etapa].evento_objetivo
        evs = ev if not r.eventos_tipo else ev[ev["tipo"].isin(r.eventos_tipo)]
        por_cli = {k: g["dia"].values for k, g in evs.groupby("entidad")}
        eleg = alc = 0
        for _, p in per.iterrows():
            dias = por_cli.get(p["id_externo"], np.array([]))
            if r.modo == "desde_alta":
                if p["fecha_alta"] + timedelta(days=r.dias) > corte:
                    continue
                n = sum(1 for d in dias if p["fecha_alta"] <= d < p["fecha_alta"] + timedelta(days=r.dias))
            else:
                lim = corte - timedelta(days=r.dias)
                if p["fecha_alta"] > lim or (pd.notna(p["fecha_baja"]) and p["fecha_baja"] <= lim):
                    continue
                n = sum(1 for d in dias if lim < d <= corte)
            eleg += 1
            alc += n >= r.min_eventos
        out[etapa] = (eleg, alc)
    venc = pg[pg["fecha_vencimiento"] <= corte]
    en_termino = venc[venc["fecha_pago"].notna() & (venc["fecha_pago"] <= venc["fecha_vencimiento"] + timedelta(days=cfg.cobranza.gracia_dias))
                      & (venc["fecha_pago"] <= corte)]
    out["cobranza"] = (len(venc), len(en_termino))
    return corte, out


@pytest.mark.parametrize("industria,estilo", [("servicios", "ar"), ("salud", "iso"), ("financiera", "ar")])
def test_kpis_coinciden_con_calculo_independiente(industria, estilo):
    demo = cargar_demo_csv(industria, n_clientes=350, semilla=5, estilo=estilo)
    ds = demo["dataset"]
    with tenant_conn(demo["tenant_id"]) as c:
        k = kpis.calcular(c)
        from datapulso.tenants import config_activa
        _, cfg = config_activa(c)
    corte, esperado = _pandas_funnel(ds, cfg)
    assert k["fecha_corte"] == str(corte)
    por_etapa = {f["etapa"]: f for f in k["funnel"]}
    for etapa, (eleg, alc) in esperado.items():
        assert (por_etapa[etapa]["elegibles"], por_etapa[etapa]["alcanzaron"]) == (eleg, alc), etapa
    assert por_etapa["alta"]["alcanzaron"] == int((ds.entidades["tipo"] == "persona").sum())
    assert all(demo["informes"][t]["filas"]["rechazadas"] == 0 for t in demo["informes"]), \
        {t: i["rechazos_por_motivo"] for t, i in demo["informes"].items()}
    # el funnel tiene sentido de negocio: hay clientes que no activan y hay mora
    assert 0 < por_etapa["activacion"]["tasa"] < 1 and 0 < por_etapa["cobranza"]["tasa"] < 1


def test_vocabulario_del_tenant_en_los_kpis():
    demo = cargar_demo_csv("salud", n_clientes=120, semilla=2)
    with tenant_conn(demo["tenant_id"]) as c:
        k = kpis.calcular(c)
        cal = kpis.calidad_datos(c)
    assert "Afiliados (total)" in k["resumen"] and "Clientes (total)" not in k["resumen"]
    assert k["industria"] == "salud" and k["es_demo"] is True
    assert cal["clientes_con_empresa"] > 0 and cal["cobertura_clientes_con_eventos_pct"] > 80
    assert k["segmentos"]["atributo"] == "plan"


def test_marcado_demo_en_todas_las_tablas():
    demo = cargar_demo_csv("servicios", n_clientes=80, semilla=1)
    with tenant_conn(demo["tenant_id"]) as c:
        for t in ("entidades", "eventos", "pagos", "fuentes", "cargas"):
            assert c.execute(f"SELECT count(*) AS n FROM {t} WHERE NOT is_demo").fetchone()["n"] == 0


# ---------------------------------------------------------------- generador
def _huella(ds):
    h = hashlib.sha256()
    for df in (ds.entidades, ds.eventos, ds.pagos):
        h.update(pd.util.hash_pandas_object(df.astype(str), index=False).values.tobytes())
    return h.hexdigest()


def test_generador_es_determinista():
    assert _huella(generar("financiera", 9, 150)) == _huella(generar("financiera", 9, 150))
    assert _huella(generar("financiera", 9, 150)) != _huella(generar("financiera", 10, 150))


@pytest.mark.parametrize("industria", ["servicios", "salud", "financiera"])
def test_la_senal_sembrada_existe(industria):
    ds = generar(industria, 21, 1200)
    v = pd.DataFrame.from_dict(ds.verdad["clientes"], orient="index")
    v["q"] = pd.qcut(v["riesgo_latente"], 5, labels=False)
    tasa = v.groupby("q")["se_dio_de_baja"].mean()
    assert tasa.iloc[-1] > 1.5 * tasa.iloc[0], tasa.to_dict()   # más riesgo latente => más bajas
    # antes de irse, usan menos: actividad del último ciclo vs ciclo 4 antes de la baja
    ev = ds.eventos.copy()
    ev["dia"] = ev["ts"].dt.normalize()
    # solo clientes que vivieron más de 180 días y activaron (si no, la ventana "antes" cae antes del alta)
    bajas = ds.entidades[(ds.entidades["estado"] == "baja") & (ds.entidades["tipo"] == "persona")]
    bajas = bajas[[(pd.Timestamp(r.fecha_baja) - pd.Timestamp(r.fecha_alta)).days >= 180
                   and not ds.verdad["clientes"][r.id_externo]["no_activa"] for r in bajas.itertuples()]]
    assert len(bajas) >= 20
    por_cli = {k: g["dia"].values for k, g in ev[ev["tipo"] != "reclamo"].groupby("entidad")}
    ult, ant = [], []
    for p in bajas.itertuples():
        d = por_cli.get(p.id_externo, np.array([], dtype="datetime64[ns]"))
        b = np.datetime64(pd.Timestamp(p.fecha_baja))
        ult.append(((d > b - np.timedelta64(30, "D")) & (d <= b)).sum())
        ant.append(((d > b - np.timedelta64(150, "D")) & (d <= b - np.timedelta64(120, "D"))).sum())
    assert np.mean(ult) < 0.6 * np.mean(ant)


def test_salud_solo_eventos_administrativos():
    ds = generar("salud", 4, 100)
    assert set(ds.eventos["tipo"].unique()) <= {"turno", "uso_servicio", "reclamo"}
    cols = " ".join(list(ds.entidades.columns) + list(ds.eventos.columns) + list(ds.pagos.columns)).lower()
    for prohibido in ("diagnost", "cie10", "patolog", "medic", "clinic"):
        assert prohibido not in cols


def test_verdad_no_esta_en_las_tablas_del_producto():
    ds = generar("servicios", 4, 60)
    cols = set(ds.entidades.columns) | set(ds.eventos.columns) | set(ds.pagos.columns)
    assert not ({"riesgo_latente", "se_dio_de_baja", "no_activa", "z"} & cols)
