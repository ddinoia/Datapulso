"""Exportación de las listas de una corrida aprobada (D9).

Dos CSV (tratamiento y control) con solo identificadores: id externo del cliente y de su empresa, grupo, acción,
canal, banda, motivo principal (driver del puntaje) y horario de contacto de la política. Sin nombres, DNI,
mail ni teléfono: el producto no los tiene. Cada archivo lleva el id de la corrida en cada fila y su hash
SHA-256 en un archivo `.sha256` al lado; la exportación queda en auditoría con los hashes.
"""
import csv
import hashlib
import io
from pathlib import Path
from ..tenants import config_activa, auditar
from . import usuarios
from .corridas import obtener, miembros, _exigir_estado

COLUMNAS = ["corrida_id", "corrida_numero", "id_externo", "empresa", "grupo", "accion", "canal", "banda",
            "motivo_principal", "horario_contacto"]
EXPORTABLES = ("aprobada", "lanzada", "medicion_en_curso", "cerrada")


def exportar(conn, ref, usuario: str, carpeta: str | Path) -> dict:
    usuarios.exigir(conn, usuario, "exportar")
    c = obtener(conn, ref)
    _exigir_estado(c, EXPORTABLES, "exportar")
    _, cfg = config_activa(conn)
    accion = conn.execute("SELECT codigo, canal FROM acciones_catalogo WHERE id = %s", (c["accion_id"],)).fetchone()
    m = miembros(conn, c["id"])
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    salida = {}
    for grupo in ("tratamiento", "control"):
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(COLUMNAS)
        for r in m[m["grupo"] == grupo].itertuples():
            w.writerow([c["id"], c["numero"], r.id_externo, r.empresa or "", grupo, accion["codigo"], accion["canal"],
                        r.banda, r.motivo or "", cfg.politica_contacto.horario_contacto])
        datos = buf.getvalue().encode("utf-8")
        h = hashlib.sha256(datos).hexdigest()
        ruta = carpeta / f"corrida_{c['numero']:04d}_{grupo}.csv"
        ruta.write_bytes(datos)
        (carpeta / (ruta.name + ".sha256")).write_text(f"{h}  {ruta.name}\n", encoding="utf-8")
        salida[grupo] = {"ruta": str(ruta), "filas": int((m["grupo"] == grupo).sum()), "sha256": h}
    auditar(conn, usuario, "corrida_exportada", {"corrida": c["numero"], "corrida_id": str(c["id"]),
                                                **{g: {"filas": v["filas"], "sha256": v["sha256"]} for g, v in salida.items()}})
    return salida
