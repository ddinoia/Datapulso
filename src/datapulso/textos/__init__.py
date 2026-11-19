"""Textos de producto en archivos (MVP: solo español). Para sumar un idioma, agregar `<idioma>.json`."""
import json
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def _cargar(idioma: str) -> dict:
    return json.loads((_DIR / f"{idioma}.json").read_text(encoding="utf-8"))


def t(clave: str, idioma: str = "es", **kw) -> str:
    """t('banda.alta') -> 'Alta prioridad'. Si falta la clave devuelve la clave (visible, no rompe)."""
    nodo = _cargar(idioma)
    for parte in clave.split("."):
        if not isinstance(nodo, dict) or parte not in nodo:
            return clave
        nodo = nodo[parte]
    return nodo.format(**kw) if kw and isinstance(nodo, str) else nodo
