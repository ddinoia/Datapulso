import pytest
from datapulso.db import tenant_conn
from datapulso.ingest.fuentes import crear_fuente, cargar_csv
from datapulso.ingest.validar import normalizar
from datapulso.ingest.mapeo import Mapeo
from datapulso.ingest.origenes import validar_consulta, OrigenError
from datetime import datetime, timezone

AHORA = datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_validar_normaliza_formatos_argentinos():
    m = Mapeo(destino="pagos", separador_decimal=",", formato_fecha="%d/%m/%Y",
              columnas={"comprobante": "c", "entidad_id_externo": "e", "fecha_emision": "fe", "fecha_vencimiento": "fv", "monto": "m"})
    f, motivo = normalizar(m, {"c": "1", "e": "X", "fe": "01/03/2026", "fv": "10/03/2026", "m": "1.234,50"}, "America/Argentina/Buenos_Aires", AHORA)
    assert motivo is None and str(f["monto"]) == "1234.50" and str(f["fecha_vencimiento"]) == "2026-03-10"
    _, motivo = normalizar(m, {"c": "1", "e": "X", "fe": "10/03/2026", "fv": "01/03/2026", "m": "5"}, "America/Argentina/Buenos_Aires", AHORA)
    assert motivo == "vencimiento_anterior_a_emision"
    _, motivo = normalizar(m, {"c": "1", "e": "X", "fe": "01/03/2026", "fv": "10/03/2026", "m": "abc"}, "America/Argentina/Buenos_Aires", AHORA)
    assert motivo == "numero_invalido:monto"
    _, motivo = normalizar(m, {"c": "", "e": "X", "fe": "01/03/2026", "fv": "10/03/2026", "m": "1"}, "America/Argentina/Buenos_Aires", AHORA)
    assert motivo == "requerido_vacio:comprobante"


def test_mapeo_incompleto_se_rechaza():
    with pytest.raises(Exception):
        Mapeo(destino="eventos", columnas={"ts": "f"})  # faltan requeridos
    with pytest.raises(Exception):
        Mapeo(destino="eventos", columnas={"ts": "f", "tipo": "t", "entidad_id_externo": "e", "inventado": "x"})


@pytest.mark.parametrize("q", ["DELETE FROM t", "SELECT 1; DROP TABLE t", "UPDATE t SET a=1", "SELECT * INTO OUTFILE '/x' FROM t",
                               "WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x", "TRUNCATE t"])
def test_consulta_de_origen_solo_select(q):
    with pytest.raises(OrigenError):
        validar_consulta(q)


def test_consulta_select_valida_pasa():
    assert validar_consulta("SELECT a, 'update' AS txt FROM v_clientes;").startswith("SELECT")


def test_carga_csv_rechazos_idempotencia_y_jerarquia(tmp_path):
    from datapulso.tenants import crear_tenant
    t = crear_tenant("CSV-1", "servicios")
    (tmp_path / "ent.csv").write_text(
        "nro;tipo;empresa;alta;plan\nE1;empresa;;2026-01-01;corp\nC1;persona;E1;2026-02-01;basico\n"
        "C2;persona;;2026-02-15;premium\nC3;persona;E9;2026-03-01;basico\nC4;persona;;mala;basico\nC2;persona;;2026-02-15;premium\n",
        encoding="utf-8")
    (tmp_path / "ev.csv").write_text(
        "id;nro;fecha;tipo;importe\n1;C1;2026-02-02 10:00:00;uso;10,50\n2;C1;2026-02-03 10:00:00;uso;\n"
        "3;C2;2026-02-20 09:00:00;uso;5\n4;ZZ;2026-02-20 09:00:00;uso;5\n", encoding="utf-8")
    with tenant_conn(t) as c:
        crear_fuente(c, nombre="ent", tipo="csv", mapeo={"destino": "entidades", "atributos": ["plan"],
                     "columnas": {"id_externo": "nro", "tipo": "tipo", "parent_id_externo": "empresa", "fecha_alta": "alta"}})
        crear_fuente(c, nombre="ev", tipo="csv", mapeo={"destino": "eventos", "separador_decimal": ",",
                     "columnas": {"id_externo": "id", "entidad_id_externo": "nro", "ts": "fecha", "tipo": "tipo", "monto": "importe"}})
        r = cargar_csv(c, "ent", tmp_path / "ent.csv")
        assert r["filas"] == {"leidas": 6, "aceptadas": 4, "rechazadas": 2, "insertadas": 4, "actualizadas": 0, "sin_cambios": 0}
        assert r["rechazos_por_motivo"] == {"fecha_invalida:fecha_alta": 1, "duplicado_en_carga": 1}
        assert any("no existe" in a for a in r["advertencias"])  # C3 apunta a E9 inexistente
        # jerarquía: C1 -> E1 resuelta; C3 sin empresa
        h = {x["id_externo"]: x["parent_id"] for x in c.execute("SELECT id_externo, parent_id FROM entidades")}
        padre = c.execute("SELECT id FROM entidades WHERE id_externo='E1'").fetchone()["id"]
        assert h["C1"] == padre and h["C3"] is None
        # los rechazados quedan en staging con motivo; los aceptados no
        stg = c.execute("SELECT nro_fila, motivo FROM stg_filas WHERE carga_id=%s ORDER BY nro_fila", (r["carga_id"],)).fetchall()
        assert [(x["nro_fila"], x["motivo"]) for x in stg] == [(5, "fecha_invalida:fecha_alta"), (6, "duplicado_en_carga")]

        r2 = cargar_csv(c, "ent", tmp_path / "ent.csv")   # idempotente
        assert r2["filas"]["insertadas"] == 0 and r2["filas"]["sin_cambios"] == 4
        e = cargar_csv(c, "ev", tmp_path / "ev.csv")
        assert e["filas"]["aceptadas"] == 3 and e["rechazos_por_motivo"] == {"entidad_desconocida": 1}
        assert c.execute("SELECT count(*) AS n FROM eventos").fetchone()["n"] == 3
        assert float(c.execute("SELECT monto FROM eventos WHERE id_externo='1'").fetchone()["monto"]) == 10.5
        # el reporte de calidad queda guardado
        assert c.execute("SELECT reporte_calidad->'filas'->>'leidas' AS n FROM cargas WHERE id=%s", (e["carga_id"],)).fetchone()["n"] == "4"


def test_carga_fallida_no_deja_datos_a_medias(tmp_path):
    from datapulso.tenants import crear_tenant
    t = crear_tenant("CSV-2", "servicios")
    (tmp_path / "x.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    with tenant_conn(t) as c:
        crear_fuente(c, nombre="ent", tipo="csv", mapeo={"destino": "entidades", "columnas": {"id_externo": "a", "fecha_alta": "b"}})
        r = cargar_csv(c, "ent", tmp_path / "x.csv")   # fecha inválida: todo rechazado, sin romper
        assert r["filas"]["aceptadas"] == 0
        with pytest.raises(FileNotFoundError):
            cargar_csv(c, "ent", tmp_path / "no_existe.csv")
        est = c.execute("SELECT estado FROM cargas ORDER BY inicio DESC LIMIT 1").fetchone()["estado"]
        assert est == "error"
