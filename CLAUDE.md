# DataPulso — contexto para el agente de código

Leé primero `PRODUCTO.md` (definición de producto, fuente de verdad) y `docs/REVISION_HITO1.md` (estado y decisiones del hito 1).

## Quién es quién
- Diego Di Noia: dueño de producto. Habla español rioplatense (voseo). Conversá con él en español; el código, los comentarios y los textos de producto también van en español (el MVP es solo ES, con textos en archivos).
- Marcelo: CTO part-time (20 hs por semana). Revisa el código después de cada hito. Por eso cada hito termina con un paquete de revisión: pruebas, guía de qué mirar y lista de decisiones que tomó el agente.
- Todo el desarrollo lo hace el agente. Las definiciones de producto no se cambian sin que Diego las confirme: si algo no está definido, proponé una opción y marcala como decisión pendiente.

## Estado
- Hito 1 (cimientos) construido y pendiente de revisión de Marcelo.
- Siguiente: hito 2 (motor de puntaje: variables, puntaje explicable, bandas, backtest temporal). Criterio de aceptación en `PRODUCTO.md`, sección 14.

## Reglas que no se rompen
- Toda tabla de negocio lleva `tenant_id` y RLS. La app usa el rol `datapulso_app` (sin BYPASSRLS); nunca el rol admin.
- Todo dato de prueba lleva `is_demo = true` y entra por la tubería real de ingesta.
- DataPulso no escribe nunca en la base del cliente. El conector es de solo lectura.
- Salud: solo eventos administrativos. Nada de diagnósticos ni datos clínicos.
- La "verdad sembrada" del generador (`verdad_sembrada.json`) nunca se carga en las tablas del producto.
- Acciones que cambian algo en el sistema del cliente nunca se ejecutan solas; requieren aprobación humana.
- La configuración es versionada e inmutable: se crea otra versión, no se edita.

## Cómo correr
```bash
pip install -e '.[dev]'
scripts/dev_env.sh start      # PostgreSQL 16 (55432) y MariaDB/MySQL (53306) locales (usa runuser; necesita root)
export DATAPULSO_SECRET_KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
datapulso migrar
pytest                         # 34 pruebas del hito 1
```
Si no tenés Docker ni permisos de root, adaptá `scripts/dev_env.sh` o usá `docker-compose.yml` (no probado).

## Al cerrar un hito
Actualizá `docs/REVISION_HITO<n>.md` con: evidencia de cada criterio de aceptación, decisiones tomadas por el agente (ordenadas por impacto), qué no está y qué no se pudo probar. Sé explícito con lo que no probaste.
