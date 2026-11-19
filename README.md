# DataPulso — hito 1: cimientos

Plataforma de ciclo de vida del cliente: analiza → entiende → recomienda → acciona → mide contra control.
Este repositorio contiene el **hito 1** (ver `docs/REVISION_HITO1.md` para la guía de revisión):
esquema multi-tenant con RLS, configuración versionada, ingesta por CSV y por conexión a base de datos
(PostgreSQL y MySQL, solo lectura, sincronización incremental), KPIs y funnel calculados desde la base,
y un generador de datos de prueba para tres industrias (servicios, salud, financiera).

La definición de producto vive en el proyecto Datapulso (`claude/producto_datapulso_v0.md`).

## Puesta en marcha

```bash
pip install -e '.[dev]'
scripts/dev_env.sh start            # PostgreSQL 16 (55432) y MariaDB/MySQL (53306) locales; o docker-compose up
export DATAPULSO_SECRET_KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
datapulso migrar
datapulso demo salud --via csv --estilo ar        # organización de demo cargada por CSV
datapulso demo servicios --via bd --motor mysql   # ...o por conexión a una base de cliente simulada
pytest                                            # pruebas de aceptación del hito 1
```

## Estructura

| Ruta | Qué hay |
|---|---|
| `db/migrations/001_base.sql` | Esquema, RLS, permisos del rol de la app |
| `src/datapulso/config.py` | Configuración por tenant (modelo validado + plantillas por industria) |
| `src/datapulso/tenants.py` | Alta de tenants, versiones de configuración, auditoría |
| `src/datapulso/ingest/` | Orígenes (CSV, BD), mapeo, validación y la tubería única |
| `src/datapulso/kpis.py` | KPIs, funnel por etapa, calidad de datos |
| `src/datapulso/datagen/` | Generador con semilla, simulador de base de cliente, demo |
| `tests/` | Aislamiento, ingesta, conector incremental, KPIs contra cálculo independiente |

Todo dato de prueba lleva `is_demo = true`. El producto no escribe nunca en la base del cliente.
