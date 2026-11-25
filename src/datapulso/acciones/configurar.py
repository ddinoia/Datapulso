"""Cambios de configuración que necesita el hito 3. Cada uno crea una versión nueva (nunca se edita una versión).
No obligan a reentrenar modelos: el proceso nocturno compara solo la parte de la configuración que afecta a cada
modelo (`puntaje.motor.huella_config`)."""
from ..tenants import config_activa, crear_version_config
from . import usuarios


def _nueva(conn, usuario: str, nota: str, cambiar) -> int:
    usuarios.exigir(conn, usuario, "administrar")
    _, cfg = config_activa(conn)
    cfg = cfg.model_copy(deep=True)
    cambiar(cfg)
    return crear_version_config(conn, type(cfg).model_validate(cfg.model_dump()), usuario, nota=nota)


def valor_economico(conn, usuario: str, etapa: str, valor: float, costo_canal: dict[str, float] | None = None) -> int:
    def f(cfg):
        cfg.valor_economico.valor_por_resultado[etapa] = float(valor)
        for canal, costo in (costo_canal or {}).items():
            cfg.valor_economico.costo_por_contacto[canal] = float(costo)
    return _nueva(conn, usuario, f"valor económico de {etapa}: {valor}", f)


def excluir(conn, usuario: str, ids_externos: list[str]) -> int:
    def f(cfg):
        cfg.politica_contacto.exclusiones = sorted(set(cfg.politica_contacto.exclusiones) | set(ids_externos))
    return _nueva(conn, usuario, f"{len(ids_externos)} clientes a la lista de exclusión", f)


def politica(conn, usuario: str, **cambios) -> int:
    """cuatro_ojos, antiguedad_max_puntajes_dias, rango_impacto (acciones) o campos de politica_contacto."""
    def f(cfg):
        for k, v in cambios.items():
            destino = cfg.acciones if hasattr(cfg.acciones, k) else cfg.politica_contacto
            setattr(destino, k, v)
    return _nueva(conn, usuario, "política: " + ", ".join(f"{k}={v}" for k, v in cambios.items()), f)
