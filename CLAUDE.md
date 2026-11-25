# DataPulso — contexto para el agente de código

Leé primero `PRODUCTO.md` (definición de producto, fuente de verdad) y las guías `docs/REVISION_HITO1.md` y `docs/REVISION_HITO2.md` (estado y decisiones de cada hito). La definición vigente es la v0.4.

## Quién es quién
- Diego Di Noia: dueño de producto. Habla español rioplatense (voseo). Conversá con él en español; el código, los comentarios y los textos de producto también van en español (el MVP es solo ES, con textos en archivos).
- Marcelo: CTO part-time (20 hs por semana). Revisa el código después de cada hito. Por eso cada hito termina con un paquete de revisión: pruebas, guía de qué mirar y lista de decisiones que tomó el agente.
- Todo el desarrollo lo hace el agente. Las definiciones de producto no se cambian sin que Diego las confirme: si algo no está definido, proponé una opción y marcala como decisión pendiente.

## Estado
- Hitos 1 (cimientos) y 2 (motor de puntaje) construidos, en `main` y pendientes de revisión de Marcelo. Pruebas a hoy: 51, todas pasan (35 en archivos del hito 1 + 16 del hito 2).
- Hito 1 verificado por Diego en Windows con Docker Desktop el 20/11/2026 (demo por CSV, KPIs y calidad de datos).
- Hito 2: sus tres decisiones de producto las aprobó Diego el 20/11/2026 (qué predice cada puntaje, horizonte por etapa, bandas por terciles con la probabilidad siempre al lado). El criterio de calibración cumple parcialmente: 7 de 8 combinaciones medibles dentro de la tolerancia propuesta (cobranza en servicios no cumple), y la tolerancia está pendiente de validación por Marcelo. Ver `docs/REVISION_HITO2.md`.
- Decisión del 20/11: el contraste proyectado vs real entra en los hitos 2 a 4. En el hito 2 hay que dejar las probabilidades calibradas y guardar versión de modelo y de configuración en cada puntaje.
- Siguiente: hito 3 (acciones: catálogo, elegibilidad, recomendación, cola con aprobación, política de contacto, exportación y webhook). Criterio de aceptación en `PRODUCTO.md`, sección 14.

## Reglas que no se rompen
- Toda tabla de negocio lleva `tenant_id` y RLS. La app usa el rol `datapulso_app` (sin BYPASSRLS); nunca el rol admin.
- Todo dato de prueba lleva `is_demo = true` y entra por la tubería real de ingesta.
- DataPulso no escribe nunca en la base del cliente. El conector es de solo lectura.
- Salud: solo eventos administrativos. Nada de diagnósticos ni datos clínicos.
- La "verdad sembrada" del generador (`verdad_sembrada.json`) nunca se carga en las tablas del producto.
- Acciones que cambian algo en el sistema del cliente nunca se ejecutan solas; requieren aprobación humana.
- La configuración es versionada e inmutable: se crea otra versión, no se edita. Lo mismo los modelos de puntaje.
- Una variable del puntaje "a la fecha c" usa solo lo conocido al final del día c (sin fuga). Si cambiás el cálculo de una variable, subí `VERSION_VARIABLES` en `puntaje/variables.py`.
- Textos de producto nuevos van en `src/datapulso/textos/es.json`, no en el código.
- La banda de un puntaje se muestra siempre con su probabilidad al lado; con la regla simple se dice que no hay probabilidad.
- No afirmes en las guías más de lo verificado: distinguí lo que probó el agente, lo que verificó Diego y lo que nadie probó.

## Cómo correr
```bash
pip install -e '.[dev]'
scripts/dev_env.sh start      # PostgreSQL 16 (55432) y MariaDB/MySQL (53306) locales (usa runuser; necesita root;
                               # si falta MariaDB: apt-get install -y mariadb-server)
export DATAPULSO_SECRET_KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
datapulso migrar
pytest                         # 51 pruebas (35 en archivos del hito 1 + 16 del hito 2), ≈ 3 min
```
Si no tenés permisos de root, adaptá `scripts/dev_env.sh` o usá `docker compose up -d`. En Windows: README, sección "Windows (PowerShell)".

## Al cerrar un hito
Actualizá `docs/REVISION_HITO<n>.md` con: evidencia de cada criterio de aceptación, decisiones tomadas por el agente (ordenadas por impacto), qué no está y qué no se pudo probar. Sé explícito con lo que no probaste.
