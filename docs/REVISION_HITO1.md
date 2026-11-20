# Hito 1 — guía de revisión para Marcelo

Estado: listo para revisar. Construido por un agente de código a partir de la definición de producto v0.2 (en ese momento `producto_datapulso_v0.md`; hoy la fuente de verdad es `PRODUCTO.md` en este repositorio).
Todo lo que sigue es lo que hay que mirar y lo que el agente decidió por su cuenta (sección 3), para que lo valides o lo cambies.

## 1. Criterio de aceptación y evidencia

> "Cargo un CSV o conecto una base de prueba y veo KPIs y funnel calculados desde la base, con el vocabulario del tenant; la segunda sincronización trae solo lo nuevo."

| Qué se pidió | Prueba que lo demuestra |
|---|---|
| CSV con rechazos explicados, idempotente, jerarquía empresa→usuarios | `tests/test_ingesta_csv.py::test_carga_csv_rechazos_idempotencia_y_jerarquia` |
| Conexión a base (PostgreSQL y MySQL): carga inicial, 2ª sincronización sin cambios trae 0 filas, 3ª trae solo el delta y actualiza lo que cambió | `tests/test_conector_bd.py::test_sincronizacion_incremental[postgresql/mysql]` |
| El conector no puede escribir en la base del cliente | `tests/test_conector_bd.py::test_usuario_de_solo_lectura_no_puede_escribir` y `test_consulta_de_origen_solo_select` |
| KPIs y funnel desde la base, iguales a un cálculo independiente en pandas (3 industrias, CSV estilo ISO y estilo AR) | `tests/test_kpis_generador.py::test_kpis_coinciden_con_calculo_independiente` |
| Vocabulario del tenant ("Afiliados" en salud) | `tests/test_kpis_generador.py::test_vocabulario_del_tenant_en_los_kpis` |
| Aislamiento por tenant (RLS), rol de la app sin BYPASSRLS, config inmutable, secretos cifrados | `tests/test_aislamiento_config.py` |
| Datos de prueba reproducibles, con señal sembrada, marcados `is_demo`, sin datos clínicos en salud | `tests/test_kpis_generador.py` (generador) |

Resultado de la corrida al cerrar el hito 1: **34 pruebas, todas pasan** (≈45 s con PostgreSQL 16 y MariaDB 10.11 locales).
Al cerrar el hito 2 se corrigió un error del generador de datos de prueba y se agregó su prueba (`test_ciclo_final_sin_actividad_inflada`): las pruebas de los archivos del hito 1 pasan a ser 35 y siguen pasando todas. Detalle en `docs/REVISION_HITO2.md`.

### Verificación de Diego (20/11/2026)

Diego corrió el hito 1 en Windows con Docker Desktop y verificó la demo por CSV, los KPIs y el reporte de calidad de datos. Eso es lo único verificado de su lado. No hay registro de `pytest` ni de la demo por conexión a base de datos en Windows.

## 2. Cómo correrlo

Ver `README.md`. Resumen: `scripts/dev_env.sh start`, `datapulso migrar`, `datapulso demo servicios --via bd --motor mysql`, `pytest`.

Medido en el entorno de construcción: ingesta ≈ 10.000 filas/s (400.000 eventos en ≈ 41 s), KPIs sobre esos 400.000 eventos en ≈ 1,2 s.

## 3. Decisiones que tomó el agente (para validar)

Ordenadas por impacto si te parecen mal:

1. **Mapeo de columnas fuera de las versiones de configuración.** La definición dice que el mapeo es configuración versionada; en este hito vive en `fuentes.mapeo` y no se versiona. Es una desviación de la especificación: conviene versionarlo antes de abrir la pantalla de Configuración.
2. **Una carga = una transacción.** Atómico y simple, pero para cargas muy grandes (millones de filas) habrá que partirlo en lotes con reanudación y cola. No se probó más allá de 400.000 filas.
3. **Marca de agua estricta (`>`) sobre una columna que el cliente expone** (fecha de modificación o id creciente). Si el cliente inserta filas con la misma marca después de una sincronización se podrían perder; mitigación incluida: `lookback_segundos` configurable (por defecto 0). Los upserts son idempotentes, así que un lookback es seguro.
4. **Seguridad de la consulta del conector:** solo `SELECT`/`WITH`, lista de palabras prohibidas, sesión abierta en modo solo lectura y usuario de BD de solo lectura. Es defensa en profundidad, **no** un parser SQL: la consulta la define DataPulso sobre vistas acordadas, no el cliente final.
5. **RLS con `ENABLE` (no `FORCE`)**: el rol dueño/admin (que migra y crea tenants) saltea RLS; la app usa un rol aparte sin privilegios. El tenant se fija a nivel de **sesión** (`set_config(..., false)`): una conexión por tenant; si se agrega un pool hay que resetearlo al devolver la conexión.
6. **SQL plano + migrador propio** (sin ORM ni Alembic) y psycopg 3 directo. Fácil de leer; si preferís Alembic se migra rápido.
7. **Se agregó `entidades.fecha_baja`** (no estaba en el modelo de la definición) porque es la etiqueta natural de churn para el hito 2.
8. **Identidad de eventos:** clave = `id_externo` del evento, o hash de (cliente, fecha-hora, tipo, monto) si no viene. Dos eventos idénticos sin id se consideran duplicado.
9. **Rechazos y staging:** las filas crudas se guardan en `stg_filas`; las aceptadas se borran al cerrar la carga y las rechazadas quedan con su motivo.
10. **Funnel:** cada etapa es una regla sobre eventos (ver docstring de `kpis.py`). Solo cuentan las personas (las empresas se excluyen). "Retención" en el funnel mide "sigue activo", no es una cohorte. Las fechas se evalúan en la zona horaria del tenant.
11. **Secretos:** credenciales cifradas con Fernet (clave en variable de entorno). La interfaz (`secrets.py`) está pensada para cambiarla por AWS Secrets Manager/KMS; **no está implementado**.
12. **Cobertura de la configuración v1:** vocabulario, etapas y reglas, ventanas de medición, segmento, control (10% / 5% cobranza), bandas, valor económico (ROI marcado como pendiente hasta cargarlo) y política de contacto. El catálogo de acciones y los roles llegan en los hitos 3 y 5.

## 4. Qué NO está (a propósito o por límite del entorno)

- API y pantallas (hito 5). Por eso no hay autenticación todavía: no se expuso nada por HTTP.
- Puntaje, recomendaciones, acciones y medición (hitos 2 a 4). El efecto de una acción **no** está aplicado en los datos de prueba; lo aplicará el simulador de resultados del hito 4 (el parámetro está en `verdad_sembrada.json`).
- `docker-compose.yml` **no lo probó el agente** (el entorno de construcción no tiene Docker). Diego usó Docker Desktop en Windows para su verificación del 20/11 (ver sección 1); no quedó registrado si el servicio MySQL del compose se usó. Se probó con PostgreSQL 16 y **MariaDB 10.11** (compatible con MySQL, pero no es MySQL 8: probar el conector contra MySQL 8.4 real, sobre todo la autenticación `caching_sha2_password`).
- SQL Server y Oracle no están soportados (decisión de producto: arrancar con PostgreSQL y MySQL).
- No se probó rendimiento ni consumo de memoria con más de 400.000 filas por carga.
- GitHub: al construir el hito 1 el acceso desde la sesión del agente no estaba habilitado y el código se entregó como bundle. Desde el hito 2 el código se entrega directamente en el repositorio `ddinoia/Datapulso`.

## 5. Qué mirar primero en la revisión (≈ 2 horas)

1. `db/migrations/001_base.sql`: esquema, políticas RLS y permisos (30 min).
2. `src/datapulso/ingest/tuberia.py` y `origenes.py`: tubería, upserts, conector, solo lectura (60 min).
3. `src/datapulso/kpis.py`: reglas de etapa y su interpretación del negocio (20 min).
4. Correr `pytest` y `datapulso demo ...` (10 min).
