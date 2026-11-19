"""Configuración de conexión a partir de variables de entorno (sin secretos en el código)."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    db_name: str
    admin_user: str
    admin_password: str
    app_user: str
    app_password: str

    def admin_kwargs(self, dbname: str | None = None) -> dict:
        d = dict(host=self.host, port=self.port, user=self.admin_user, dbname=dbname or self.db_name)
        if self.admin_password:
            d["password"] = self.admin_password
        return d

    def app_kwargs(self) -> dict:
        return dict(host=self.host, port=self.port, user=self.app_user,
                    password=self.app_password, dbname=self.db_name)


def get_settings() -> Settings:
    e = os.environ.get
    return Settings(
        host=e("DP_PG_HOST", "127.0.0.1"),
        port=int(e("DP_PG_PORT", "55432")),
        db_name=e("DP_DB_NAME", "datapulso"),
        admin_user=e("DP_ADMIN_USER", "postgres"),
        admin_password=e("DP_ADMIN_PASSWORD", ""),
        app_user=e("DP_APP_USER", "datapulso_app"),
        app_password=e("DP_APP_PASSWORD", "dev-only-cambiar"),
    )
