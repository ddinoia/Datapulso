"""Salida por webhook o tarea humana hacia el sistema del cliente (D9).

Solo si el tenant habilitó una salida y la corrida está aprobada (o lanzada). Nunca se emite antes de la
aprobación ni se emite sola: la emisión es un comando explícito de un aprobador o administrador.

Cada POST lleva:
  X-DataPulso-Timestamp  segundos Unix
  X-DataPulso-Firma      "sha256=" + HMAC-SHA256(secreto, "<timestamp>." + cuerpo)
  Idempotency-Key        mismo valor en cada reintento y en cada reemisión del mismo lote
Solo viaja el grupo de tratamiento, en lotes de LOTE clientes. Reintentos con espera exponencial ante errores de
red, 408, 429 y 5xx; un 4xx distinto se registra como fallido sin reintentar. Cada intento queda en
`entregas_webhook`. Un lote ya entregado no se vuelve a mandar.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import secrets as _secrets
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from ..secrets import cifrar, descifrar
from ..tenants import config_activa, auditar
from ..textos import t
from . import ErrorAcciones, usuarios
from .corridas import obtener, miembros, _exigir_estado

LOTE = 500
EMITIBLES = ("aprobada", "lanzada", "medicion_en_curso")
TIPOS_EMITIBLES = ("webhook", "tarea_humana")
_LOCALES = {"localhost", "127.0.0.1", "::1"}


def configurar_salida(conn, nombre: str, url: str, usuario: str, secreto: str | None = None,
                      tipos: tuple[str, ...] = TIPOS_EMITIBLES) -> tuple[dict, str]:
    """Habilita una salida. Devuelve (salida, secreto): el secreto se muestra una sola vez y se guarda cifrado."""
    usuarios.exigir(conn, usuario, "administrar")
    u = urlparse(url)
    if not (u.scheme == "https" or (u.scheme == "http" and u.hostname in _LOCALES)):
        raise ErrorAcciones(t("acciones.error.url"))
    secreto = secreto or _secrets.token_urlsafe(32)
    demo = conn.execute("SELECT is_demo FROM tenants").fetchone()["is_demo"]
    s = conn.execute("""INSERT INTO salidas (tenant_id, nombre, url, secreto_cifrado, tipos, is_demo)
                        VALUES (app_tenant(), %s, %s, %s, %s, %s) RETURNING *""",
                     (nombre, url, cifrar(secreto), list(tipos), demo)).fetchone()
    auditar(conn, usuario, "salida_habilitada", {"salida": nombre, "url": url, "tipos": list(tipos)})
    return s, secreto


def firmar(secreto: str, timestamp: str, cuerpo: bytes) -> str:
    return "sha256=" + hmac.new(secreto.encode(), timestamp.encode() + b"." + cuerpo, hashlib.sha256).hexdigest()


def _post(url: str, cuerpo: bytes, headers: dict, timeout: float) -> int:
    req = urllib.request.Request(url, data=cuerpo, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def emitir(conn, ref, usuario: str, salida: str | None = None, reintentos: int = 3, espera_base: float = 1.0,
           timeout: float = 10.0) -> dict:
    usuarios.exigir(conn, usuario, "emitir")
    c = obtener(conn, ref)
    _exigir_estado(c, EMITIBLES, "emitir")
    accion = conn.execute("SELECT * FROM acciones_catalogo WHERE id = %s", (c["accion_id"],)).fetchone()
    if accion["tipo"] not in TIPOS_EMITIBLES:
        raise ErrorAcciones(t("acciones.error.tipo_no_emitible", tipo=t("acciones.tipo." + accion["tipo"])))
    s = conn.execute("""SELECT * FROM salidas WHERE habilitada AND %s = ANY(tipos) AND (%s::text IS NULL OR nombre = %s)
                        ORDER BY creado_en LIMIT 1""", (accion["tipo"], salida, salida)).fetchone()
    if not s:
        raise ErrorAcciones(t("acciones.error.sin_salida", tipo=t("acciones.tipo." + accion["tipo"])))
    _, cfg = config_activa(conn)
    secreto = descifrar(s["secreto_cifrado"])
    m = miembros(conn, c["id"])
    trat = m[m["grupo"] == "tratamiento"]
    lotes = [trat.iloc[i:i + LOTE] for i in range(0, len(trat), LOTE)]
    demo = conn.execute("SELECT is_demo FROM tenants").fetchone()["is_demo"]
    resumen = {"lotes": len(lotes), "entregados": 0, "ya_entregados": 0, "fallidos": 0, "intentos": 0}
    for k, lote in enumerate(lotes, start=1):
        cuerpo = json.dumps({
            "evento": f"datapulso.{accion['tipo']}",
            "corrida": {"id": str(c["id"]), "numero": c["numero"], "etapa": c["etapa"], "estado": c["estado"]},
            "accion": {"codigo": accion["codigo"], "version": accion["version"], "nombre": accion["nombre"],
                       "tipo": accion["tipo"], "canal": accion["canal"], "plantilla": accion["plantilla"],
                       "requiere_confirmacion_humana": bool(accion["modifica_sistema_cliente"])},
            "horario_contacto": cfg.politica_contacto.horario_contacto,
            "lote": k, "lotes": len(lotes),
            "clientes": [{"id_externo": r.id_externo, "empresa": r.empresa or None, "banda": r.banda, "motivo": r.motivo}
                         for r in lote.itertuples()],
        }, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
        h = hashlib.sha256(cuerpo).hexdigest()
        clave = hashlib.sha256(f"{c['id']}:{s['id']}:{k}:{h}".encode()).hexdigest()[:40]
        if conn.execute("SELECT 1 FROM entregas_webhook WHERE clave_idempotencia = %s AND estado = 'entregada'",
                        (clave,)).fetchone():
            resumen["ya_entregados"] += 1
            continue
        for intento in range(1, reintentos + 1):
            ts = str(int(time.time()))
            headers = {"Content-Type": "application/json", "X-DataPulso-Timestamp": ts,
                       "X-DataPulso-Firma": firmar(secreto, ts, cuerpo), "Idempotency-Key": clave}
            codigo, error = None, None
            try:
                codigo = _post(s["url"], cuerpo, headers, timeout)
            except (urllib.error.URLError, OSError) as e:
                error = str(e)[:300]
            ok = codigo is not None and 200 <= codigo < 300
            conn.execute("""INSERT INTO entregas_webhook (tenant_id, corrida_id, salida_id, lote, clave_idempotencia, intento,
                                                          estado, codigo_http, error, hash_cuerpo, is_demo)
                            VALUES (app_tenant(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                         (c["id"], s["id"], k, clave, intento, "entregada" if ok else "fallida", codigo, error, h, demo))
            conn.commit()      # cada intento queda registrado aunque el proceso se corte después
            resumen["intentos"] += 1
            if ok:
                resumen["entregados"] += 1
                break
            reintentable = codigo is None or codigo in (408, 429) or codigo >= 500
            if not reintentable or intento == reintentos:
                resumen["fallidos"] += 1
                break
            time.sleep(espera_base * 2 ** (intento - 1))
    auditar(conn, usuario, "corrida_emitida", {"corrida": c["numero"], "salida": s["nombre"], **resumen})
    return resumen
