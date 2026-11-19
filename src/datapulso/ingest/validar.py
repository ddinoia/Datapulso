"""Validación y normalización de una fila cruda -> fila canónica. Devuelve (fila, None) o (None, motivo)."""
import hashlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from .mapeo import Mapeo, CAMPOS

CAMPOS_FECHA = {"fecha_alta", "fecha_baja", "fecha_emision", "fecha_vencimiento", "fecha_pago"}
CAMPOS_NUM = {"monto", "monto_pagado"}


class Rechazo(Exception):
    def __init__(self, motivo: str):
        self.motivo = motivo


def _vacio(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _fecha(v, fmt, campo) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    try:
        if fmt:
            return datetime.strptime(s, fmt).date()
        return datetime.fromisoformat(s).date() if len(s) > 10 else date.fromisoformat(s)
    except ValueError:
        raise Rechazo(f"fecha_invalida:{campo}")


def _ts(v, fmt, tz: ZoneInfo, campo) -> datetime:
    if isinstance(v, datetime):
        d = v
    elif isinstance(v, date):
        d = datetime(v.year, v.month, v.day)
    else:
        s = str(v).strip()
        try:
            d = datetime.strptime(s, fmt) if fmt else datetime.fromisoformat(s)
        except ValueError:
            raise Rechazo(f"fecha_invalida:{campo}")
    return d if d.tzinfo else d.replace(tzinfo=tz)


def _num(v, sep, campo) -> Decimal:
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        return Decimal(str(v))
    s = str(v).strip().replace(" ", "")
    if sep == ",":
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise Rechazo(f"numero_invalido:{campo}")
    if not d.is_finite():
        raise Rechazo(f"numero_invalido:{campo}")
    return d


def _texto(v):
    if _vacio(v):
        return None
    return str(v).strip()


def normalizar(mapeo: Mapeo, crudo: dict, tz_tenant: str, ahora: datetime):
    """Devuelve (fila_canonica, None) o (None, motivo). Sin efectos secundarios."""
    tz = ZoneInfo(mapeo.zona_horaria or tz_tenant)
    spec = CAMPOS[mapeo.destino]
    try:
        out: dict = {}
        for campo, col in mapeo.columnas.items():
            v = crudo.get(col)
            if _vacio(v):
                if campo in spec["requeridos"]:
                    raise Rechazo(f"requerido_vacio:{campo}")
                out[campo] = None
                continue
            if campo in mapeo.valores:
                v = mapeo.valores[campo].get(str(v).strip(), v)
            if campo in CAMPOS_FECHA:
                out[campo] = _fecha(v, mapeo.formato_fecha, campo)
            elif campo == "ts":
                out[campo] = _ts(v, mapeo.formato_ts, tz, campo)
            elif campo in CAMPOS_NUM:
                out[campo] = _num(v, mapeo.separador_decimal, campo)
            else:
                out[campo] = _texto(v)
        attrs = {}
        for col in mapeo.atributos:
            v = crudo.get(col)
            if not _vacio(v):
                attrs[col] = v if isinstance(v, (int, float, bool)) else str(v).strip()
        out["atributos"] = attrs
        _reglas(mapeo.destino, out, ahora)
        return out, None
    except Rechazo as r:
        return None, r.motivo


def _reglas(destino: str, f: dict, ahora: datetime) -> None:
    hoy = ahora.date()
    if destino == "entidades":
        t = (f.get("tipo") or "persona").lower()
        if t not in ("persona", "empresa"):
            raise Rechazo("tipo_invalido")
        f["tipo"] = t
        if f["fecha_alta"] > hoy + timedelta(days=1):
            raise Rechazo("fecha_futura:fecha_alta")
        if f.get("fecha_baja") and f["fecha_baja"] < f["fecha_alta"]:
            raise Rechazo("baja_anterior_al_alta")
    elif destino == "eventos":
        if f["ts"] > ahora + timedelta(days=1):
            raise Rechazo("fecha_futura:ts")
        f["clave"] = f.get("id_externo") or hashlib.sha1(
            f"{f['entidad_id_externo']}|{f['ts'].astimezone(timezone.utc).isoformat()}|{f['tipo']}|{f.get('monto')}".encode()
        ).hexdigest()
    elif destino == "pagos":
        if f["fecha_vencimiento"] < f["fecha_emision"]:
            raise Rechazo("vencimiento_anterior_a_emision")
        if f.get("monto_pagado") is not None and f["monto_pagado"] < 0:
            raise Rechazo("monto_pagado_negativo")
        if f["monto"] < 0:
            raise Rechazo("monto_negativo")


def clave_dedupe(destino: str, f: dict) -> str:
    return {"entidades": lambda: f["id_externo"],
            "eventos": lambda: f["clave"],
            "pagos": lambda: f["comprobante"]}[destino]()
