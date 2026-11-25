"""Hito 3 · acciones: ciclo completo, inmutabilidad después de aprobar, nada sale sin aprobación, valor económico
obligatorio, asignación reproducible y balanceada, política de contacto, proyección contra cálculo manual,
recomendación, permisos y cuatro ojos, puntajes viejos, RLS y webhook firmado con reintentos e idempotencia."""
import csv
import hashlib
import hmac
import json
import threading
import time
import uuid
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import numpy as np
import pandas as pd
import psycopg
import pytest
from datapulso.db import tenant_conn, admin_conn
from datapulso.datagen.demo import cargar_demo_csv
from datapulso.puntaje import motor
from datapulso.tenants import config_activa
from datapulso.acciones import ErrorAcciones, usuarios, catalogo, configurar, recomendacion, corridas, exportar, webhook
from datapulso.acciones.plantillas import PLANTILLAS

VALOR_RET = 60000.0


def _org(semilla: int, valores: dict) -> str:
    tid = cargar_demo_csv("servicios", n_clientes=1500, semilla=semilla)["tenant_id"]
    with tenant_conn(tid) as c:
        for etapa in ("retencion", "cobranza"):
            m = motor.entrenar(c, etapa)
            motor.puntuar(c, etapa, modelo_version=m["version"])
        usuarios.alta(c, "admin", "administrador", "bootstrap")
        usuarios.alta(c, "ana", "analista", "admin")
        usuarios.alta(c, "apro", "aprobador", "admin")
        usuarios.alta(c, "lector", "solo_lectura", "admin")
        catalogo.cargar_plantilla(c, "servicios", "admin")
        for etapa, v in valores.items():
            configurar.valor_economico(c, "admin", etapa, v)
        c.commit()
    return tid


@pytest.fixture(scope="module")
def org():
    """Organización principal. Cobranza queda SIN valor económico a propósito."""
    return _org(11, {"retencion": VALOR_RET})


@pytest.fixture(scope="module")
def org2():
    """Segunda organización: política de contacto, webhook y aislamiento."""
    return _org(12, {"retencion": VALOR_RET, "cobranza": 30000.0})


def _cancelar_abiertas(c, etapa=None):
    for x in corridas.listar(c):
        if x["estado"] in ("borrador", "pendiente_aprobacion", "aprobada") and (etapa is None or x["etapa"] == etapa):
            corridas.cancelar(c, x["numero"], "admin", "limpieza de la prueba")
    c.commit()


# ---------------------------------------------------------------- ciclo completo (criterio de aceptación)
def test_ciclo_completo_retencion_servicios(org, tmp_path):
    """Recomendar, crear, pedir aprobación, aprobar (proyección congelada), exportar y lanzar."""
    with tenant_conn(org) as c:
        rec = recomendacion.recomendar(c, "retencion", limite=50)
        assert rec["valor_configurado"] and rec["clientes"]
        alta = next(cl for cl in rec["clientes"] if cl["banda"] == "alta")
        assert "RET_LLAMADA" in [o["codigo"] for o in alta["opciones"]]

        t0 = time.perf_counter()
        cr = corridas.crear(c, "RET_LLAMADA", "ana", semilla=2026)
        c.commit()
        segundos = time.perf_counter() - t0
        print(f"\n[rendimiento] crear corrida RET_LLAMADA sobre servicios (1.500 clientes): {segundos:.2f} s")
        assert segundos < 10
        assert cr["estado"] == "borrador" and cr["proyeccion"]["congelada"] is False

        corridas.pedir_aprobacion(c, cr["numero"], "ana")
        assert [x["numero"] for x in corridas.cola_aprobacion(c)] == [cr["numero"]]
        ap = corridas.aprobar(c, cr["numero"], "apro")
        c.commit()
        p = ap["proyeccion"]
        assert ap["estado"] == "aprobada" and p["congelada"] is True and p["semilla"] == 2026
        assert p["versiones"]["modelo"] == cr["modelo_version"] and p["versiones"]["config"] == config_activa(c)[0]
        assert p["accion"]["impacto_origen"] == "supuesto" and p["esperado"]["roi_esperado"] is not None
        assert p["rango"]["bajo"]["eventos_evitados"] < p["esperado"]["eventos_evitados"] < p["rango"]["alto"]["eventos_evitados"]

        archivos = exportar.exportar(c, cr["numero"], "ana", tmp_path)
        m = corridas.miembros(c, cr["id"])
        for grupo, info in archivos.items():
            ruta = Path(info["ruta"])
            datos = ruta.read_bytes()
            assert hashlib.sha256(datos).hexdigest() == info["sha256"]
            assert (ruta.parent / (ruta.name + ".sha256")).read_text().split()[0] == info["sha256"]
            filas = list(csv.DictReader(datos.decode().splitlines()))
            assert {f["id_externo"] for f in filas} == set(m[m["grupo"] == grupo]["id_externo"])
            assert all(f["corrida_id"] == str(cr["id"]) and f["grupo"] == grupo for f in filas)
            # solo identificadores: ninguna columna de datos personales
            assert set(filas[0]) == set(exportar.COLUMNAS)
            assert not {"nombre", "dni", "email", "mail", "telefono", "direccion"} & set(filas[0])
            assert not any(v.lower() == "nan" for f in filas for v in f.values())
            assert any(f["empresa"] == "" for f in filas) and any(f["empresa"] for f in filas)
        assert archivos["control"]["filas"] == round(len(m) * 0.10)

        lz = corridas.lanzar(c, cr["numero"], "ana", date.today())
        c.commit()
        assert lz["estado"] == "lanzada" and lz["fecha_lanzamiento"] == date.today()
        acciones_aud = [r["accion"] for r in c.execute(
            "SELECT accion FROM auditoria WHERE detalle->>'corrida' = %s ORDER BY id", (str(cr["numero"]),))]
        assert acciones_aud == ["corrida_creada", "corrida_aprobacion_pedida", "corrida_aprobada", "corrida_exportada",
                                "corrida_lanzada"]


# ---------------------------------------------------------------- inmutabilidad después de aprobar
def test_corrida_aprobada_no_se_puede_modificar(org):
    with tenant_conn(org) as c:
        _cancelar_abiertas(c, "retencion")
        cr = corridas.crear(c, "RET_MENSAJE", "ana", bandas=["media"], limite=200, semilla=7)
        corridas.pedir_aprobacion(c, cr["numero"], "ana")
        ap = corridas.aprobar(c, cr["numero"], "apro")
        c.commit()
        proy = ap["proyeccion"]
        miembro = c.execute("SELECT entidad_id FROM corrida_entidades WHERE corrida_id = %s LIMIT 1", (cr["id"],)).fetchone()
        otro = c.execute("""SELECT id FROM entidades WHERE tipo='persona' AND id NOT IN
                            (SELECT entidad_id FROM corrida_entidades) LIMIT 1""").fetchone()["id"]
        intentos = [
            ("UPDATE corridas SET proyeccion = '{}' WHERE id = %s", (cr["id"],)),
            ("UPDATE corridas SET semilla = 1 WHERE id = %s", (cr["id"],)),
            ("UPDATE corridas SET estado = 'borrador' WHERE id = %s", (cr["id"],)),
            ("UPDATE corrida_entidades SET grupo = 'control' WHERE corrida_id = %s", (cr["id"],)),
            ("DELETE FROM corrida_entidades WHERE corrida_id = %s", (cr["id"],)),
            ("""INSERT INTO corrida_entidades (corrida_id, tenant_id, entidad_id, etapa, grupo, banda, segmento, puntaje)
                VALUES (%s, app_tenant(), %s, 'retencion', 'control', 'media', 'x', 0.1)""", (cr["id"], otro)),
        ]
        for sql, params in intentos:
            with pytest.raises(psycopg.errors.RaiseException):
                c.execute(sql, params)
            c.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):     # la app ni siquiera puede borrar corridas
            c.execute("DELETE FROM corridas WHERE id = %s", (cr["id"],))
        c.rollback()
        assert miembro
        # ni con el rol dueño de las tablas
        with admin_conn() as adm:
            for sql in ("UPDATE corridas SET proyeccion = '{}' WHERE id = %s", "DELETE FROM corridas WHERE id = %s",
                        "UPDATE corrida_entidades SET grupo = 'control' WHERE corrida_id = %s"):
                with pytest.raises(psycopg.errors.RaiseException):
                    adm.execute(sql, (cr["id"],))
                adm.rollback()
        # ni cambiando la configuración ni el catálogo después
        configurar.valor_economico(c, "admin", "retencion", VALOR_RET * 3)
        a = catalogo.vigente(c, "RET_MENSAJE")
        catalogo.guardar(c, {**{k: a[k] for k in ("codigo", "nombre", "etapa", "tipo", "canal", "plantilla",
                                                  "elegibilidad", "frecuencia_max_dias", "modifica_sistema_cliente")},
                             "costo_unitario": 999, "impacto_esperado": 0.5}, "admin")
        c.commit()
        assert corridas.obtener(c, cr["numero"])["proyeccion"] == proy
        configurar.valor_economico(c, "admin", "retencion", VALOR_RET)
        original = next(x for x in PLANTILLAS["servicios"] if x["codigo"] == "RET_MENSAJE")
        catalogo.guardar(c, original, "admin")          # versión 3 = valores de la plantilla otra vez
        corridas.cancelar(c, cr["numero"], "apro", "fin de la prueba")
        c.commit()


# ---------------------------------------------------------------- nada sale sin aprobación
def test_sin_aprobacion_no_hay_exportacion_ni_webhook_ni_lanzamiento(org, tmp_path):
    with tenant_conn(org) as c:
        _cancelar_abiertas(c, "retencion")
        webhook.configurar_salida(c, "crm", "http://127.0.0.1:9/nunca", "admin")
        catalogo.guardar(c, _tarea_crm(), "admin")
        cr = corridas.crear(c, "RET_TAREA_CRM", "ana", limite=50, semilla=3)
        c.commit()
        for estado in ("borrador", "pendiente_aprobacion"):
            with pytest.raises(ErrorAcciones, match="estado"):
                exportar.exportar(c, cr["numero"], "ana", tmp_path)
            with pytest.raises(ErrorAcciones, match="estado"):
                webhook.emitir(c, cr["numero"], "apro", reintentos=1, espera_base=0)
            with pytest.raises(ErrorAcciones, match="estado"):
                corridas.lanzar(c, cr["numero"], "ana", date.today())
            if estado == "borrador":
                corridas.pedir_aprobacion(c, cr["numero"], "ana")
                c.commit()
        assert not list(tmp_path.iterdir())
        assert c.execute("SELECT count(*) AS n FROM entregas_webhook").fetchone()["n"] == 0
        corridas.cancelar(c, cr["numero"], "apro", "fin de la prueba")
        c.commit()


def test_sin_valor_economico_no_se_aprueba(org):
    with tenant_conn(org) as c:
        cr = corridas.crear(c, "COB_RECORDATORIO", "ana", limite=300, semilla=5)
        corridas.pedir_aprobacion(c, cr["numero"], "ana")
        c.commit()
        with pytest.raises(ErrorAcciones, match="falta el valor económico .*cobranza"):
            corridas.aprobar(c, cr["numero"], "apro")
        c.rollback()
        assert corridas.obtener(c, cr["numero"])["estado"] == "pendiente_aprobacion"
        configurar.valor_economico(c, "admin", "cobranza", 25000)
        ap = corridas.aprobar(c, cr["numero"], "apro")
        c.commit()
        # cobranza: 5 % de control
        assert ap["estado"] == "aprobada" and ap["proyeccion"]["tamanos"]["control"] == round(300 * 0.05)
        # cambiar el valor económico no reentrena modelos: solo crea otra versión de configuración
        r = {x["etapa"]: x for x in motor.proceso_nocturno(c) if "omitida" not in x}
        assert r and not any(x["entrenado"] for x in r.values() if x["etapa"] in ("retencion", "cobranza"))
        corridas.cancelar(c, cr["numero"], "apro", "fin de la prueba")
        c.commit()


# ---------------------------------------------------------------- asignación
def test_asignacion_reproducible_y_balanceada():
    rng = np.random.default_rng(0)
    n = 2347
    ids = [f"C{i:05d}" for i in range(n)]
    estratos = list(rng.choice(["alta·a", "alta·b", "media·a", "media·b", "baja·c"], n, p=[.4, .05, .3, .2, .05]))
    for pct in (10.0, 5.0):
        g1 = corridas.asignar_grupos(ids, estratos, pct, 123)
        assert list(g1) == list(corridas.asignar_grupos(ids, estratos, pct, 123))           # misma semilla
        assert list(g1) != list(corridas.asignar_grupos(ids, estratos, pct, 124))           # otra semilla
        assert list(g1) == list(corridas.asignar_grupos(ids[::-1], estratos[::-1], pct, 123)[::-1])  # no depende del orden
        df = pd.DataFrame({"e": estratos, "g": g1})
        assert (df["g"] == "control").sum() == round(n * pct / 100)                         # proporción exacta
        por = df.groupby("e")["g"].apply(lambda s: (s == "control").sum())
        cuota = df.groupby("e").size() * pct / 100
        assert ((por - cuota).abs() < 1).all(), (por - cuota)                               # balance: < 1 cliente por estrato


def test_asignacion_en_una_corrida_real(org):
    with tenant_conn(org) as c:
        _cancelar_abiertas(c, "retencion")
        a = corridas.crear(c, "RET_MENSAJE", "ana", semilla=99)
        m1 = corridas.miembros(c, a["id"])
        corridas.cancelar(c, a["numero"], "ana", "repetir con la misma semilla")
        b = corridas.crear(c, "RET_MENSAJE", "ana", semilla=99)
        m2 = corridas.miembros(c, b["id"])
        c.commit()
        assert m1[["id_externo", "grupo"]].reset_index(drop=True).equals(m2[["id_externo", "grupo"]].reset_index(drop=True))
        n = len(m1)
        assert (m1["grupo"] == "control").sum() == round(n * 0.10)
        for e in b["diseno"]["estratos"]:
            assert abs(e["control"] - (e["control"] + e["tratamiento"]) * 0.10) < 1
        corridas.cancelar(c, b["numero"], "ana", "fin de la prueba")
        c.commit()


# ---------------------------------------------------------------- política de contacto
def test_politica_de_contacto(org2):
    with tenant_conn(org2) as c:
        _, cfg = config_activa(c)
        sin_consent = {r["id_externo"] for r in c.execute(
            "SELECT id_externo FROM entidades WHERE tipo='persona' AND atributos->>'consentimiento_contacto' = 'no'")}
        assert sin_consent
        excluir = sorted(c.execute("""SELECT e.id_externo FROM puntajes p JOIN entidades e ON e.id = p.entidad_id
                                       WHERE p.etapa='retencion' AND p.banda='alta'
                                         AND e.atributos->>'consentimiento_contacto'='si' ORDER BY 1 LIMIT 5""").fetchall()
                         , key=lambda r: r["id_externo"])
        excluir = [r["id_externo"] for r in excluir]
        configurar.excluir(c, "admin", excluir)
        configurar.politica(c, "admin", max_contactos_por_cliente_30d=1)

        # 1) corrida de cobranza aprobada: sus tratados quedan con 1 contacto en 30 días
        cob = corridas.crear(c, "COB_RECORDATORIO", "ana", semilla=1)
        corridas.pedir_aprobacion(c, cob["numero"], "ana")
        corridas.aprobar(c, cob["numero"], "apro")
        mc = corridas.miembros(c, cob["id"])
        trat_cob, ctrl_cob = set(mc[mc["grupo"] == "tratamiento"]["id_externo"]), set(mc[mc["grupo"] == "control"]["id_externo"])

        # 2) corrida abierta de retención (borrador) que ocupa a algunos clientes de banda alta
        abierta = corridas.crear(c, "RET_LLAMADA", "ana", limite=30, semilla=2)
        ocupados = set(corridas.miembros(c, abierta["id"])["id_externo"])

        # 3) nueva corrida de retención: debe dejar afuera a cada uno por su motivo
        nueva = corridas.crear(c, "RET_MENSAJE", "ana", semilla=3)
        c.commit()
        mn = set(corridas.miembros(c, nueva["id"])["id_externo"])
        excl = {r["id_externo"]: r["motivo"] for r in c.execute(
            """SELECT e.id_externo, x.motivo FROM corrida_excluidos x JOIN entidades e ON e.id = x.entidad_id
               WHERE x.corrida_id = %s""", (nueva["id"],))}
        assert not (mn & sin_consent) and not (mn & set(excluir)) and not (mn & ocupados)
        assert all(excl[i] == "corrida_abierta" for i in ocupados if i in excl) and ocupados <= set(excl)
        assert all(excl.get(i) == "lista_exclusion" for i in excluir if i not in ocupados)
        assert {excl[i] for i in sin_consent if i in excl} <= {"sin_consentimiento", "corrida_abierta", "lista_exclusion"}
        tope = {i for i, mo in excl.items() if mo == "tope_contactos"}
        assert tope and tope <= trat_cob                     # solo cuentan los tratados de la corrida anterior
        assert not (ctrl_cob & tope) and (ctrl_cob & mn)      # el control no consume el tope
        assert nueva["exclusiones"]["por_motivo"] == pd.Series(list(excl.values())).value_counts().to_dict()

        # la base también impide meter a un cliente en dos corridas abiertas de la misma etapa
        repetido = c.execute("SELECT id FROM entidades WHERE id_externo = %s", (sorted(ocupados)[0],)).fetchone()["id"]
        corridas.cancelar(c, nueva["numero"], "ana", "liberar clientes para la verificación siguiente")
        configurar.politica(c, "admin", max_contactos_por_cliente_30d=2)
        otra = corridas.crear(c, "RET_LLAMADA", "ana", bandas=["alta"], limite=20, semilla=4)
        with pytest.raises(psycopg.errors.RaiseException, match="corrida abierta"):
            c.execute("""INSERT INTO corrida_entidades (corrida_id, tenant_id, entidad_id, etapa, grupo, banda, segmento, puntaje)
                         VALUES (%s, app_tenant(), %s, 'retencion', 'tratamiento', 'alta', 'x', 0.5)""", (otra["id"], repetido))
        c.rollback()
        _cancelar_abiertas(c)
        c.commit()


# ---------------------------------------------------------------- proyección contra cálculo manual
def test_proyeccion_coincide_con_calculo_manual(org):
    with tenant_conn(org) as c:
        _cancelar_abiertas(c, "retencion")
        # banda media: la banda alta está ocupada por la corrida lanzada del ciclo completo (D4)
        cr = corridas.crear(c, "RET_MENSAJE", "ana", bandas=["media"], limite=40, semilla=11)
        corridas.pedir_aprobacion(c, cr["numero"], "ana")
        p = corridas.aprobar(c, cr["numero"], "apro")["proyeccion"]
        c.commit()
        m = corridas.miembros(c, cr["id"])
        m["puntaje"] = m["puntaje"].astype(float)
        t_ = m[m["grupo"] == "tratamiento"]["puntaje"]
        n_t, n_c = len(t_), int((m["grupo"] == "control").sum())
        i, costo_u = 0.04, 40.0                            # RET_MENSAJE de la plantilla de servicios
        tasa = t_.mean()
        evit = t_.sum() * i
        valor = evit * VALOR_RET
        costo = costo_u * n_t
        assert (n_t, n_c) == (36, 4)                       # 40 clientes, 10 % de control
        assert p["tasa_esperada_sin_accion"]["tratamiento"] == pytest.approx(tasa, abs=1e-6)
        assert p["tasa_esperada_sin_accion"]["total"] == pytest.approx(m["puntaje"].mean(), abs=1e-6)
        assert p["esperado"]["efecto_esperado"] == pytest.approx(tasa * i, abs=1e-6)
        assert p["esperado"]["eventos_evitados"] == pytest.approx(evit, abs=1e-3)
        assert p["esperado"]["valor_esperado"] == pytest.approx(valor, abs=0.01)
        assert p["costo_total"] == pytest.approx(costo)
        assert p["esperado"]["roi_esperado"] == pytest.approx((valor - costo) / costo, abs=1e-4)
        assert p["rango"]["impacto"] == pytest.approx([i * 0.5, i * 1.5])
        assert p["rango"]["bajo"]["roi_esperado"] == pytest.approx((evit * 0.5 * VALOR_RET - costo) / costo, abs=1e-4)
        q = m["puntaje"].mean()
        emd = (1.959964 + 0.841621) * np.sqrt(q * (1 - q) * (1 / n_t + 1 / n_c))
        assert cr["diseno"]["efecto_minimo_detectable"] == pytest.approx(emd, abs=1e-5)
        assert cr["diseno"]["aviso"]                        # con 4 de control, casi seguro no concluyente
        corridas.cancelar(c, cr["numero"], "apro", "fin de la prueba")
        c.commit()


# ---------------------------------------------------------------- recomendación
def test_recomendacion_ordenada_por_roi_y_explicable(org):
    with tenant_conn(org) as c:
        r = recomendacion.recomendar(c, "retencion", limite=3000)
        acciones = {a["codigo"]: a for a in catalogo.vigentes(c, "retencion")}
    assert r["valor_configurado"]
    for cl in r["clientes"]:
        rois = [o["roi_esperado"] for o in cl["opciones"]]
        assert rois == sorted(rois, reverse=True) and len(cl["opciones"]) <= 3
        for o in cl["opciones"]:
            a = acciones[o["codigo"]]
            esperado = cl["puntaje"] * float(a["impacto_esperado"]) * VALOR_RET - float(a["costo_unitario"])
            assert o["roi_esperado"] == pytest.approx(esperado)
            assert "supuesto, no medido" in o["motivo"]
            assert ("requiere confirmación humana" in o["motivo"]) == a["modifica_sistema_cliente"]
            assert cl["banda"] in (a["elegibilidad"]["bandas"] or [cl["banda"]])          # elegibilidad por banda
    assert any(cl["banda"] == "baja" for cl in r["clientes"])
    assert all("RET_LLAMADA" not in [o["codigo"] for o in cl["opciones"]] for cl in r["clientes"] if cl["banda"] != "alta")


# ---------------------------------------------------------------- permisos, cuatro ojos y puntajes viejos
def test_permisos_cuatro_ojos_y_puntajes_viejos(org):
    with tenant_conn(org) as c:
        _cancelar_abiertas(c, "retencion")
        with pytest.raises(ErrorAcciones, match="no puede crear corridas"):
            corridas.crear(c, "RET_MENSAJE", "lector")
        with pytest.raises(ErrorAcciones, match="No existe el usuario"):
            corridas.crear(c, "RET_MENSAJE", "fantasma")
        cr = corridas.crear(c, "RET_MENSAJE", "admin", limite=100, semilla=1)
        corridas.pedir_aprobacion(c, cr["numero"], "admin")
        with pytest.raises(ErrorAcciones, match="no puede aprobar"):
            corridas.aprobar(c, cr["numero"], "ana")
        # por defecto quien crea puede aprobar, y queda registrado
        ap = corridas.aprobar(c, cr["numero"], "admin")
        assert ap["proyeccion"]["creada_por_misma_persona"] is True
        corridas.cancelar(c, cr["numero"], "admin", "fin")
        # con cuatro ojos, no
        configurar.politica(c, "admin", cuatro_ojos=True)
        cr2 = corridas.crear(c, "RET_MENSAJE", "admin", limite=100, semilla=1)
        corridas.pedir_aprobacion(c, cr2["numero"], "admin")
        with pytest.raises(ErrorAcciones, match="cuatro ojos"):
            corridas.aprobar(c, cr2["numero"], "admin")
        assert corridas.aprobar(c, cr2["numero"], "apro")["estado"] == "aprobada"
        corridas.cancelar(c, cr2["numero"], "apro", "fin")
        configurar.politica(c, "admin", cuatro_ojos=False)
        # puntajes viejos: la corrida pendiente no se aprueba y no se pueden crear corridas nuevas
        cr3 = corridas.crear(c, "RET_MENSAJE", "ana", limite=100, semilla=1)
        corridas.pedir_aprobacion(c, cr3["numero"], "ana")
        c.commit()
        with admin_conn() as adm:
            adm.execute("UPDATE puntajes SET creado_en = now() - interval '9 days' WHERE tenant_id = %s", (org,))
            adm.execute("""UPDATE corridas SET diseno = jsonb_set(diseno, '{puntajes_calculados_en}',
                           to_jsonb((now() - interval '9 days')::text)) WHERE id = %s""", (cr3["id"],))
            adm.commit()
        try:
            with pytest.raises(ErrorAcciones, match="hace 9 días"):
                corridas.aprobar(c, cr3["numero"], "apro")
            c.rollback()
            with pytest.raises(ErrorAcciones, match="hace 9 días"):
                corridas.crear(c, "RET_MENSAJE", "ana")
            c.rollback()
        finally:
            with admin_conn() as adm:
                adm.execute("UPDATE puntajes SET creado_en = now() WHERE tenant_id = %s", (org,))
                adm.commit()
        corridas.cancelar(c, cr3["numero"], "ana", "puntajes viejos")
        c.commit()
        transiciones = [r["accion"] for r in c.execute("SELECT accion FROM auditoria WHERE accion LIKE 'corrida_%'")]
        assert transiciones.count("corrida_cancelada") >= 3


# ---------------------------------------------------------------- RLS
def test_aislamiento_de_acciones_corridas_y_entregas(org, org2):
    tablas = ("usuarios", "acciones_catalogo", "corridas", "corrida_entidades", "corrida_excluidos", "salidas",
              "entregas_webhook")
    with tenant_conn(org) as c:
        assert c.execute("SELECT count(*) AS n FROM corridas").fetchone()["n"] > 0
        for t in tablas:
            assert c.execute(f"SELECT count(*) AS n FROM {t} WHERE tenant_id = %s", (org2,)).fetchone()["n"] == 0
            assert c.execute(f"SELECT count(*) AS n FROM {t} WHERE NOT is_demo").fetchone()["n"] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("INSERT INTO usuarios (tenant_id, nombre, rol) VALUES (%s, 'intruso', 'administrador')", (org2,))
        c.rollback()
    with tenant_conn(org2) as c2:     # la otra organización sí tiene lo suyo (no es que esté vacía)
        assert c2.execute("SELECT count(*) AS n FROM corridas").fetchone()["n"] > 0
        assert c2.execute("SELECT count(*) AS n FROM corridas WHERE tenant_id = %s", (org,)).fetchone()["n"] == 0


# ---------------------------------------------------------------- webhook
def _tarea_crm() -> dict:
    return {"codigo": "RET_TAREA_CRM", "nombre": "Tarea en el CRM del cliente para el equipo de retención",
            "etapa": "retencion", "tipo": "tarea_humana", "canal": "crm", "costo_unitario": 800, "impacto_esperado": 0.10,
            "elegibilidad": {"bandas": ["alta", "media"]}, "modifica_sistema_cliente": True}


def _json_estricto(x):
    raise ValueError(f"JSON inválido: {x}")


class _Receptor:
    """Receptor local: verifica la firma, registra claves de idempotencia y falla las primeras `fallar` veces."""

    def __init__(self, secreto: str, fallar: int = 1):
        self.secreto, self.fallar, self.recibidos = secreto, fallar, []
        receptor = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                cuerpo = self.rfile.read(int(self.headers["Content-Length"]))
                ts = self.headers["X-DataPulso-Timestamp"]
                esperada = "sha256=" + hmac.new(receptor.secreto.encode(), ts.encode() + b"." + cuerpo,
                                                hashlib.sha256).hexdigest()
                receptor.recibidos.append({"firma_ok": hmac.compare_digest(esperada, self.headers["X-DataPulso-Firma"]),
                                           "clave": self.headers["Idempotency-Key"],
                                           "cuerpo": json.loads(cuerpo, parse_constant=_json_estricto)})
                codigo = 503 if receptor.fallar > 0 else 200
                receptor.fallar -= 1
                self.send_response(codigo)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/datapulso"


def test_webhook_firmado_idempotente_con_reintentos(org2, monkeypatch):
    monkeypatch.setattr(webhook, "LOTE", 100)
    with tenant_conn(org2) as c:
        with pytest.raises(ErrorAcciones, match="https"):
            webhook.configurar_salida(c, "insegura", "http://ejemplo.com/hook", "admin")
        with pytest.raises(ErrorAcciones, match="no puede administrar"):
            webhook.configurar_salida(c, "x", "https://ejemplo.com/hook", "ana")
        catalogo.guardar(c, _tarea_crm(), "admin")
        cr = corridas.crear(c, "RET_TAREA_CRM", "ana", limite=250, semilla=8)
        c.commit()
        with pytest.raises(ErrorAcciones, match="estado"):          # antes de aprobar: nada
            webhook.emitir(c, cr["numero"], "apro", espera_base=0)
        corridas.pedir_aprobacion(c, cr["numero"], "ana")
        corridas.aprobar(c, cr["numero"], "apro")
        c.commit()
        with pytest.raises(ErrorAcciones, match="no habilitó una salida"):
            webhook.emitir(c, cr["numero"], "apro", espera_base=0)
        _, secreto = webhook.configurar_salida(c, "crm", "http://127.0.0.1:1/placeholder", "admin")
        receptor = _Receptor(secreto, fallar=1)
        c.execute("UPDATE salidas SET habilitada = false WHERE nombre = 'crm'")
        webhook.configurar_salida(c, "crm_local", receptor.url, "admin", secreto=secreto)
        c.commit()
        with pytest.raises(ErrorAcciones, match="no puede emitir"):
            webhook.emitir(c, cr["numero"], "ana", espera_base=0)
        r = webhook.emitir(c, cr["numero"], "apro", espera_base=0.01)
        m = corridas.miembros(c, cr["id"])
        trat, ctrl = set(m[m["grupo"] == "tratamiento"]["id_externo"]), set(m[m["grupo"] == "control"]["id_externo"])
        lotes = -(-len(trat) // 100)
        assert r == {"lotes": lotes, "entregados": lotes, "ya_entregados": 0, "fallidos": 0, "intentos": lotes + 1}
        assert len(receptor.recibidos) == lotes + 1 and all(x["firma_ok"] for x in receptor.recibidos)
        assert receptor.recibidos[0]["clave"] == receptor.recibidos[1]["clave"]        # el reintento usa la misma clave
        enviados = {cl["id_externo"] for x in receptor.recibidos[1:] for cl in x["cuerpo"]["clientes"]}
        assert enviados == trat and not (enviados & ctrl)                                # el control nunca sale
        assert all(x["cuerpo"]["accion"]["requiere_confirmacion_humana"] for x in receptor.recibidos)
        # reemitir no duplica: los lotes entregados no se vuelven a mandar
        r2 = webhook.emitir(c, cr["numero"], "apro", espera_base=0)
        assert r2["ya_entregados"] == lotes and r2["intentos"] == 0 and len(receptor.recibidos) == lotes + 1
        filas = c.execute("SELECT estado, count(*) AS n FROM entregas_webhook WHERE corrida_id = %s GROUP BY 1",
                          (cr["id"],)).fetchall()
        assert {f["estado"]: f["n"] for f in filas} == {"fallida": 1, "entregada": lotes}
        # el registro de entregas no se edita
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE entregas_webhook SET estado = 'entregada'")
        c.rollback()
        # acciones de tipo mensaje no salen por webhook (se ejecutan con la exportación)
        corridas.cancelar(c, cr["numero"], "apro", "fin de la prueba")
        msg = corridas.crear(c, "RET_MENSAJE", "ana", limite=20, semilla=1)
        corridas.pedir_aprobacion(c, msg["numero"], "ana")
        corridas.aprobar(c, msg["numero"], "apro")
        with pytest.raises(ErrorAcciones, match="exportación"):
            webhook.emitir(c, msg["numero"], "apro")
        corridas.cancelar(c, msg["numero"], "apro", "fin de la prueba")
        c.commit()
        srv = receptor.srv
    srv.shutdown()


def test_webhook_4xx_no_se_reintenta(org2):
    with tenant_conn(org2) as c:
        _cancelar_abiertas(c, "retencion")
        cr = corridas.crear(c, "RET_TAREA_CRM", "ana", limite=20, semilla=9)
        corridas.pedir_aprobacion(c, cr["numero"], "ana")
        corridas.aprobar(c, cr["numero"], "apro")
        c.execute("UPDATE salidas SET habilitada = false")
        _, secreto = webhook.configurar_salida(c, f"rechaza_{uuid.uuid4().hex[:4]}", "http://127.0.0.1:1/x", "admin")
        c.commit()

        class R(BaseHTTPRequestHandler):
            n = 0

            def do_POST(self):
                R.n += 1
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(401)
                self.end_headers()

            def log_message(self, *a):
                pass
        srv = ThreadingHTTPServer(("127.0.0.1", 0), R)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        c.execute("UPDATE salidas SET url = %s WHERE habilitada", (f"http://127.0.0.1:{srv.server_port}/x",))
        c.commit()
        r = webhook.emitir(c, cr["numero"], "apro", reintentos=3, espera_base=0)
        assert r["fallidos"] == 1 and r["intentos"] == 1 and R.n == 1
        corridas.cancelar(c, cr["numero"], "apro", "fin de la prueba")
        c.commit()
        srv.shutdown()


# ---------------------------------------------------------------- plantillas
def test_plantillas_de_salud_solo_administrativas_e_impacto_supuesto():
    for industria, acciones in PLANTILLAS.items():
        assert {a["etapa"] for a in acciones} == {"retencion", "cobranza", "activacion"}
        for a in acciones:
            catalogo.AccionDef.model_validate(a)
            assert 0 < a["impacto_esperado"] < 1
    texto = json.dumps(PLANTILLAS["salud"], ensure_ascii=False).lower()
    for prohibido in ("diagnost", "tratamiento médico", "medicaci", "patolog", "clínic", "síntoma", "receta"):
        assert prohibido not in texto, prohibido
