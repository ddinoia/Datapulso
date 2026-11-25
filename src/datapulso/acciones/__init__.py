"""Hito 3 · acciones: catálogo, elegibilidad y recomendación, política de contacto, corridas con aprobación,
proyección congelada, exportación y webhook.

DataPulso recomienda y deja la lista lista para ejecutar; nunca ejecuta nada dentro del sistema del cliente.
Todo lo que sale (exportación, webhook) exige una corrida aprobada por una persona.
"""


class ErrorAcciones(Exception):
    """Error de negocio con un mensaje para mostrar tal cual (textos/es.json)."""
