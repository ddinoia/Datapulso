"""Orígenes de filas. Todos entregan dicts a la MISMA tubería (staging -> mapeo -> validación -> canónicas).
Agregar un origen nuevo (p. ej. API) = una clase nueva con `filas()`; la tubería no cambia."""
import csv
import re
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from typing import Iterator


class OrigenError(Exception):
    pass


# ------------------------------------------------------------------ CSV
class OrigenCsv:
    tipo = "csv"

    def __init__(self, ruta: str | Path, encoding: str = "utf-8-sig", delimitador: str | None = None):
        self.ruta = Path(ruta)
        self.encoding = encoding
        self.delimitador = delimitador
        self.marca_nueva: str | None = None
        self.descripcion = self.ruta.name

    def filas(self, marca_antes: str | None = None) -> Iterator[dict]:
        with open(self.ruta, newline="", encoding=self.encoding) as fh:
            muestra = fh.read(4096)
            fh.seek(0)
            delim = self.delimitador
            if not delim:
                try:
                    delim = csv.Sniffer().sniff(muestra, delimiters=",;\t|").delimiter
                except csv.Error:
                    delim = ","
            lector = csv.DictReader(fh, delimiter=delim)
            if not lector.fieldnames:
                raise OrigenError("El CSV no tiene encabezados")
            for fila in lector:
                fila.pop(None, None)  # columnas de más en una fila
                yield fila


# ------------------------------------------------------------------ Base de datos (solo lectura)
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_PROHIBIDAS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|merge|call|copy|replace|"
    r"outfile|dumpfile|load_file|pg_sleep|lock|set|execute|exec)\b", re.I)


def validar_consulta(q: str) -> str:
    """La consulta la define DataPulso/el cliente sobre una vista; aun así se restringe a un SELECT.
    Defensa en profundidad: además, la sesión se abre en modo solo lectura y el usuario debe ser de solo lectura."""
    s = q.strip().rstrip(";").strip()
    if ";" in s:
        raise OrigenError("La consulta debe ser una única sentencia")
    if not re.match(r"^(select|with)\b", s, re.I):
        raise OrigenError("La consulta debe empezar con SELECT o WITH")
    sin_literales = re.sub(r"'(?:[^']|'')*'", "''", s)
    sin_literales = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sin_literales, flags=re.S)
    m = _PROHIBIDAS.search(sin_literales)
    if m:
        raise OrigenError(f"Palabra no permitida en la consulta: {m.group(0)}")
    return s


class OrigenBaseDatos:
    tipo = "base_de_datos"

    def __init__(self, *, motor: str, host: str, puerto: int, base: str, usuario: str, password: str,
                 consulta: str, marca_columna: str | None = None, marca_tipo: str = "fecha",
                 lookback_segundos: int = 0, tamano_lote: int = 5000):
        if motor not in ("postgresql", "mysql"):
            raise OrigenError(f"Motor no soportado: {motor} (soportados: postgresql, mysql)")
        if marca_columna and not _IDENT.match(marca_columna):
            raise OrigenError("Nombre de columna de marca de agua inválido")
        if marca_tipo not in ("fecha", "entero"):
            raise OrigenError("marca_tipo debe ser 'fecha' o 'entero'")
        self.motor, self.host, self.puerto, self.base = motor, host, puerto, base
        self.usuario, self.password = usuario, password
        self.consulta = validar_consulta(consulta)
        self.marca_columna, self.marca_tipo = marca_columna, marca_tipo
        self.lookback = lookback_segundos
        self.tamano_lote = tamano_lote
        self.marca_nueva: str | None = None
        self.descripcion = f"{motor}://{host}:{puerto}/{base} · {self.consulta[:80]}"

    # --- marca de agua
    def _parse_marca(self, s: str):
        if self.marca_tipo == "entero":
            return int(s)
        d = datetime.fromisoformat(s)
        if self.lookback:
            from datetime import timedelta
            d = d - timedelta(seconds=self.lookback)
        return d

    @staticmethod
    def _ser_marca(v) -> str:
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        return str(int(v)) if isinstance(v, (int, Decimal)) else str(v)

    def _sql(self, marca_antes: str | None):
        base = f"SELECT * FROM ({self.consulta}) AS _q"
        params: list = []
        if self.marca_columna:
            if marca_antes:
                base += f" WHERE _q.{self.marca_columna} > %s"
                params.append(self._parse_marca(marca_antes))
            base += f" ORDER BY _q.{self.marca_columna}"
        return base, params

    # --- conexión de solo lectura
    def _abrir(self):
        if self.motor == "postgresql":
            import psycopg
            c = psycopg.connect(host=self.host, port=self.puerto, dbname=self.base, user=self.usuario,
                                password=self.password or None, connect_timeout=15)
            c.read_only = True
            return c
        import pymysql
        c = pymysql.connect(host=self.host, port=self.puerto, database=self.base, user=self.usuario,
                            password=self.password, connect_timeout=15, cursorclass=pymysql.cursors.SSDictCursor)
        with c.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
        return c

    def probar(self, n: int = 5) -> dict:
        """Prueba de conexión + muestra (para el asistente de mapeo)."""
        sql, params = self._sql(None)
        conn = self._abrir()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM ({self.consulta}) AS _q LIMIT {int(n)}")
            filas = self._a_dicts(cur, cur.fetchall())
            return {"columnas": list(filas[0].keys()) if filas else [], "muestra": filas}
        finally:
            conn.close()

    @staticmethod
    def _a_dicts(cur, filas):
        if filas and isinstance(filas[0], dict):
            return filas
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, f)) for f in filas]

    def filas(self, marca_antes: str | None = None) -> Iterator[dict]:
        sql, params = self._sql(marca_antes)
        maxv = None
        conn = self._abrir()
        try:
            if self.motor == "postgresql":
                cur = conn.cursor(name="dp_lectura")  # cursor del lado servidor: no carga todo en memoria
                cur.itersize = self.tamano_lote
            else:
                cur = conn.cursor()
            cur.execute(sql, params)
            cols = None
            while True:
                lote = cur.fetchmany(self.tamano_lote)
                if not lote:
                    break
                if cols is None and not isinstance(lote[0], dict):
                    cols = [d[0] for d in cur.description]
                for r in lote:
                    d = r if isinstance(r, dict) else dict(zip(cols, r))
                    if self.marca_columna:
                        v = d.get(self.marca_columna)
                        if v is not None and (maxv is None or v > maxv):
                            maxv = v
                    yield d
        finally:
            conn.rollback()
            conn.close()
        if maxv is not None:
            self.marca_nueva = self._ser_marca(maxv)
        else:
            self.marca_nueva = marca_antes
