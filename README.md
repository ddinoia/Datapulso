# DataPulso — hitos 1 (cimientos) y 2 (motor de puntaje)

Plataforma de ciclo de vida del cliente: analiza → entiende → recomienda → acciona → mide contra control.
- **Hito 1** (`docs/REVISION_HITO1.md`): esquema multi-tenant con RLS, configuración versionada, ingesta por CSV y por
  conexión a base de datos (PostgreSQL y MySQL, solo lectura, sincronización incremental), KPIs y funnel calculados
  desde la base, y un generador de datos de prueba para tres industrias (servicios, salud, financiera).
- **Hito 2** (`docs/REVISION_HITO2.md`): motor de puntaje por etapa (retención, cobranza, activación, uso y valor):
  variables a fecha de corte sin fuga, regresión logística explicable con drivers por cliente, regla simple de respaldo,
  bandas por terciles con la probabilidad al lado, backtest temporal, calibración medida (y Platt si hace falta)
  y proceso nocturno.

La definición de producto es `PRODUCTO.md` (fuente de verdad); el contexto para agentes de código, `CLAUDE.md`.

## Puesta en marcha

```bash
pip install -e '.[dev]'
scripts/dev_env.sh start            # PostgreSQL 16 (55432) y MariaDB/MySQL (53306) locales; o docker compose up -d
export DATAPULSO_SECRET_KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
datapulso migrar
datapulso demo salud --via csv --estilo ar        # organización de demo cargada por CSV
datapulso demo servicios --via bd --motor mysql   # ...o por conexión a una base de cliente simulada
datapulso tenants                                 # organizaciones: nombre, id, industria, demo, config, fecha
datapulso puntuar "<tenant>"                      # backtest + entrenamiento + puntajes de todas las etapas
datapulso clientes "<tenant>" --etapa retencion    # lista con puntaje, banda y drivers
pytest                                            # pruebas de aceptación de los hitos 1 y 2
```

## Windows (PowerShell)

Requisitos: Python 3.11 o superior (con la opción "Add python.exe to PATH"), Git y Docker Desktop abierto.
Diego verificó el hito 1 en Windows con Docker Desktop el 20/11/2026: demo por CSV, KPIs y calidad de datos.
El resto no se probó en Windows: `datapulso tenants` (nuevo), la demo por conexión a MySQL, el puntaje del hito 2
y `pytest`.

```powershell
# 1. Código y entorno de Python
git clone https://github.com/ddinoia/Datapulso.git
cd Datapulso
py -m venv .venv
.\.venv\Scripts\Activate.ps1          # si PowerShell lo bloquea: Set-ExecutionPolicy -Scope Process Bypass
pip install -e ".[dev]"

# 2. Bases de datos locales (PostgreSQL 16 en 55432 y MySQL 8.4 en 53306)
docker compose up -d

# 3. Clave para cifrar credenciales (vale para esta ventana de PowerShell; repetir en cada ventana nueva)
$env:DATAPULSO_SECRET_KEY = python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 4. Esquema y una organización de demo
datapulso migrar
datapulso demo salud --via csv
datapulso tenants                                    # nombre, id, industria, demo, versión de config, fecha
datapulso kpis "<nombre que mostró tenants>"
datapulso calidad "<nombre>"

# 5. Puntaje (hito 2)
datapulso puntuar "<nombre>"
datapulso clientes "<nombre>" --etapa retencion --banda alta --limite 10
datapulso modelo "<nombre>" --etapa retencion

# Para apagar las bases (los datos se pierden con `docker compose down -v`)
docker compose stop
```

Notas:
- Entre comillas va el nombre completo de la organización tal como lo muestra `datapulso tenants` (por ejemplo `"Demo salud 7a55"`); también se puede usar el id.
- `scripts/dev_env.sh` es solo para Linux; en Windows se usa `docker compose`.
- Si `datapulso` no se reconoce, revisar que el entorno esté activado (el prompt empieza con `(.venv)`).

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
