"""Cifrado de credenciales de fuentes.

Desarrollo: clave Fernet en la variable DATAPULSO_SECRET_KEY.
Producción (a definir con Marcelo): reemplazar por AWS Secrets Manager / KMS manteniendo esta interfaz.
Nunca se guardan contraseñas en claro ni en `parametros`.
"""
import os
from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = os.environ.get("DATAPULSO_SECRET_KEY")
    if not key:
        raise RuntimeError("Falta DATAPULSO_SECRET_KEY (clave Fernet). Generar con: "
                           "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'")
    return Fernet(key.encode())


def cifrar(texto: str) -> bytes:
    return _fernet().encrypt(texto.encode())


def descifrar(blob: bytes) -> str:
    return _fernet().decrypt(bytes(blob)).decode()
