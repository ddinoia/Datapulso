"""Multi-tenant (RLS), configuración versionada e inmutable, secretos."""
import pytest
import psycopg
from datapulso.db import tenant_conn, admin_conn
from datapulso.settings import get_settings
from datapulso.tenants import crear_tenant, crear_version_config, config_activa
from datapulso.config import plantilla, pendientes, ConfigTenant
from datapulso.ingest.fuentes import crear_fuente


def _entidad(c, ext):
    c.execute("INSERT INTO entidades (tenant_id, id_externo, fecha_alta) VALUES (app_tenant(), %s, '2026-01-01')", (ext,))
    c.commit()


def test_rol_de_app_no_salta_rls():
    with admin_conn() as c:
        r = c.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=%s", (get_settings().app_user,)).fetchone()
    assert r["rolsuper"] is False and r["rolbypassrls"] is False


def test_cada_tenant_ve_solo_lo_suyo():
    a, b = crear_tenant("RLS-A", "servicios"), crear_tenant("RLS-B", "salud")
    with tenant_conn(a) as ca:
        _entidad(ca, "A-1")
    with tenant_conn(b) as cb:
        _entidad(cb, "B-1")
        _entidad(cb, "B-2")
    with tenant_conn(a) as ca:
        assert [r["id_externo"] for r in ca.execute("SELECT id_externo FROM entidades")] == ["A-1"]
        assert ca.execute("SELECT count(*) AS n FROM tenants").fetchone()["n"] == 1
    with tenant_conn(b) as cb:
        assert cb.execute("SELECT count(*) AS n FROM entidades").fetchone()["n"] == 2


def test_sin_tenant_no_se_ve_nada():
    s = get_settings()
    with psycopg.connect(**s.app_kwargs()) as c:
        assert c.execute("SELECT count(*) FROM entidades").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM tenants").fetchone()[0] == 0


def test_no_se_puede_escribir_en_otro_tenant():
    a, b = crear_tenant("RLS-C", "servicios"), crear_tenant("RLS-D", "servicios")
    with tenant_conn(a) as ca:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ca.execute("INSERT INTO entidades (tenant_id, id_externo, fecha_alta) VALUES (%s, 'X', '2026-01-01')", (b,))


def test_config_versionada_e_inmutable():
    t = crear_tenant("CFG-1", "salud")
    with tenant_conn(t) as c:
        v, cfg = config_activa(c)
        assert v == 1 and cfg.vocabulario.clientes == "Afiliados"
        nueva = cfg.model_copy(deep=True)
        nueva.control_pct = 12.0
        v2 = crear_version_config(c, nueva, "diego", "control 12%")
        c.commit()
        assert v2 == 2
        v_act, cfg_act = config_activa(c)
        assert v_act == 2 and cfg_act.control_pct == 12.0
        # la versión 1 sigue intacta y no se puede tocar
        assert c.execute("SELECT contenido->>'control_pct' AS p FROM config_versions WHERE version=1").fetchone()["p"] == "10.0"
        # la app ni siquiera tiene permiso de modificar versiones ni auditoría...
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE config_versions SET nota='x' WHERE version=1")
        c.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM auditoria")
        c.rollback()
    # ...y aunque se use el rol dueño, un trigger las bloquea (defensa en profundidad)
    with admin_conn() as a:
        with pytest.raises(psycopg.errors.RaiseException):
            a.execute("UPDATE config_versions SET nota='x' WHERE tenant_id=%s", (t,))
        a.rollback()
        with pytest.raises(psycopg.errors.RaiseException):
            a.execute("DELETE FROM auditoria WHERE tenant_id=%s", (t,))


def test_config_invalida_se_rechaza_y_roi_queda_pendiente():
    with pytest.raises(Exception):
        ConfigTenant.model_validate({"etapas": {"inventada": {}}})
    with pytest.raises(Exception):
        ConfigTenant.model_validate({"etapas": {"activacion": {"activa": True}}})  # falta evento objetivo
    cfg = plantilla("financiera")
    assert any("ROI" in p for p in pendientes(cfg))  # ROI obligatorio: hasta cargar el valor, figura como pendiente
    for k in cfg.etapas:
        cfg.valor_economico.valor_por_resultado[k] = 1000.0
    cfg.valor_economico.costo_por_contacto = {"mensaje": 5.0}
    assert pendientes(cfg) == []


def test_credenciales_cifradas_y_no_en_parametros():
    t = crear_tenant("SEC-1", "servicios")
    mapeo = {"destino": "entidades", "columnas": {"id_externo": "a", "fecha_alta": "b"}}
    base = {"motor": "postgresql", "host": "h", "puerto": 5432, "base": "b", "usuario": "u", "consulta": "SELECT * FROM t"}
    with tenant_conn(t) as c:
        with pytest.raises(ValueError):
            crear_fuente(c, nombre="x", tipo="base_de_datos", mapeo=mapeo, parametros={**base, "password": "secreta"})
        crear_fuente(c, nombre="ok", tipo="base_de_datos", mapeo=mapeo, parametros=base, password="clave-super-secreta")
        r = c.execute("SELECT parametros::text AS p, credencial_cifrada AS k FROM fuentes WHERE nombre='ok'").fetchone()
        assert "clave-super-secreta" not in r["p"]
        assert b"clave-super-secreta" not in bytes(r["k"])
