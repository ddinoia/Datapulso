# DataPulso — hitos 1 (cimientos) y 2 (motor de puntaje)

Plataforma de ciclo de vida del cliente: analiza → entiende → recomienda → acciona → mide contra control.
- **Hito 1** (`docs/REVISION_HITO1.md`): esquema multi-tenant con RLS, configuración versionada, ingesta por CSV y por
  conexión a base de datos (PostgreSQL y MySQL, solo lectura, sincronización incremental), KPIs y funnel calculados
  desde la base, y un generador de datos de prueba para tres industrias (servicios, salud, financiera).
- **Hito 2** (`docs/REVISION_HITO2.md`): motor de puntaje por etapa (retención, cobranza, activación, uso y valor):
  variables a fecha de corte sin fuga, regresión logística explicable con drivers por cliente, regla simple de respaldo,
  bandas por terciles, backtest temporal y proceso nocturno.

La definición de producto es `PRODUCTO.md` (fuente de verdad); el contexto para agentes de código, `CLAUDE.md`.

## Puesta en marcha

```bash
pip install -e '.[dev]'
scripts/dev_env.sh start            # PostgreSQL 16 (55432) y MariaDB/MySQL (53306) locales; o docker-compose up
export DATAPULSO_SECRET_KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
datapulso migrar
datapulso demo salud --via csv --estilo ar        # organización de demo cargada por CSV
datapulso demo servicios --via bd --motor mysql   # ...o por conexión a una base de cliente simulada
datapulso puntuar "<tenant>"                      # backtest + entrenamiento + puntajes de todas las etapas
datapulso clientes "<tenant>" --etapa retencion    # lista con puntaje, banda y drivers
pytest                                            # pruebas de aceptación de los hitos 1 y 2
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
| `db/migrations/002_puntaje.sql` | Tablas `modelos` (inmutable) y `puntajes`, con RLS |
| `src/datapulso/puntaje/` | Variables, etapas (qué se predice), modelo y regla, motor (backtest, proceso nocturno), presentación |
| `src/datapulso/textos/es.json` | Textos de producto del hito 2 (MVP solo español) |
| `tests/` | Aislamiento, ingesta, conector incremental, KPIs contra cálculo independiente, puntaje contra la verdad sembrada |

Todo dato de prueba lleva `is_demo = true`. El producto no escribe nunca en la base del cliente.
