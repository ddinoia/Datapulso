# DataPulso — Definición de producto v0.4

Estado: decisiones de producto confirmadas por Diego el 19/11/2026 y el 20/11/2026 (ver sección 15). Dueño de producto: Diego. Dueño técnico: Marcelo (CTO, part-time, 20 hs por semana).
Modo de desarrollo: todo el código lo desarrollan agentes; Marcelo lo revisa después de cada hito.
Reemplaza como fuente de verdad a los demos HTML (que se descartan; solo valen como referencia de requisitos).
Avance: hitos 1 y 2 construidos y listos para revisión de Marcelo; guías en `docs/REVISION_HITO1.md` y `docs/REVISION_HITO2.md`. Diego verificó el hito 1 en Windows con Docker Desktop el 20/11/2026 (demo por CSV, KPIs y calidad de datos).
Novedad v0.4 (20/11/2026): se incorpora el contraste entre lo proyectado y lo medido (secciones 8, 9, 10 y 14), idea de Diego.

## 1. Qué es

Plataforma multi-industria que sigue el ciclo de vida del cliente de una empresa y **cierra el ciclo**: analiza, explica, recomienda, acciona y mide el resultado de la acción contra un grupo de control.

El diferencial es el resultado medible. Todo lo demás (tableros, alertas, forecast) es soporte. No es un BPM: se conecta con cualquier sistema (salida) y cualquier fuente (entrada), a demanda por cliente.

Se vende la solución completa. En cada piloto el cliente elige qué etapa medir primero.

## 2. Alcance del MVP

Dentro: hitos 1 a 4 (ver sección 14) más una aplicación mínima con Panorama, Clientes, Acciones, Resultados, Configuración y Datos. La entrada de datos incluye CSV y conexión directa a bases de datos (PostgreSQL y MySQL) desde el primer hito.

Fuera del MVP (después): aprendizaje con ajuste propuesto del impacto esperado a partir de lo medido (el contraste proyectado vs real sí entra en el MVP), conectores por API, otros motores de base de datos (SQL Server, Oracle), consulta en lenguaje natural, alertas con contexto externo, forecast, WhatsApp/email integrados, portal para clientes del cliente, optimización por uplift.

## 3. Vocabulario unificado

| Concepto | Término del producto |
|---|---|
| Persona o empresa que compra/usa | Cliente (tipo: persona o empresa) |
| Empresa que contrata DataPulso | Tenant (en pantalla: "Organización") |
| Etapa del ciclo | Alta, Activación, Uso y valor, Retención, Cobranza |
| Probabilidad del evento objetivo | Puntaje, agrupado en Bandas (alta, media, baja prioridad) |
| Intervención | Acción |
| Tanda de una acción sobre un grupo | Corrida |
| Grupo que no recibe la acción | Control |

Cada tenant puede renombrar estos términos (ver configuración). Términos específicos de finanzas (NPL, NIM, vintage, fondeo, TNA) no están en el núcleo; si un cliente los necesita, son métricas opcionales.

## 4. Modelo de datos (mínimo)

Todas las tablas llevan `tenant_id`. Aislamiento por fila (RLS) desde el día 1. Todas las filas de prueba llevan `is_demo = true`.

- `tenants`: id, nombre, idioma, moneda, zona_horaria, config_version_activa.
- `entidades` (clientes): id, id_externo, tipo [persona|empresa], parent_id (empresa a la que pertenece; la jerarquía empresa-usuarios está en el modelo desde el MVP), fecha_alta, fecha_baja (agregada en el hito 1: etiqueta natural de churn), estado_externo, atributos (jsonb: segmento, plan, ciudad, etc.).
- `eventos` (movimientos, compras, usos, contactos): id, entidad_id, ts, tipo, monto, moneda, canal, categoria, atributos (jsonb).
- `pagos` (facturación y cobros): id, entidad_id, comprobante, fecha_emision, fecha_vencimiento, monto, fecha_pago, monto_pagado.
- `fuentes`: id, tenant_id, tipo [csv|base_de_datos|api], parametros (jsonb; credenciales cifradas, nunca en claro), mapeo, frecuencia, marca_de_agua (última fila o fecha sincronizada), estado.
- `cargas`: id, fuente_id, inicio, fin, filas_leidas, filas_aceptadas, filas_rechazadas, reporte_calidad (jsonb).
- `config_versions`: id, tenant_id, version, contenido (jsonb), creado_por, fecha. Nunca se edita una versión: se crea otra.
- `puntajes`: entidad_id, etapa, fecha, puntaje, banda, drivers (jsonb), modelo_version.
- `acciones_catalogo`: ver sección 7.
- `corridas`: id, accion_id, creada_por, aprobada_por, estado, fecha_lanzamiento, pct_control, semilla, config_version, proyeccion (jsonb, congelada al aprobar: tasa esperada sin acción, efecto esperado, valor y ROI esperados, versión del modelo y de la configuración; no se puede editar).
- `corrida_entidades`: corrida_id, entidad_id, grupo [tratamiento|control], contactado_ts, estado.
- `resultados`: corrida_id, ventana_dias, n_trat, n_ctrl, tasa_trat, tasa_ctrl, diferencia, ic_inf, ic_sup, valor_incremental, costo, roi, concluyente; y el contraste con la proyección: desvío del modelo (tasa esperada sin acción vs tasa real del control) y desvío de la acción (efecto esperado vs diferencia medida).
- `auditoria`: quién, qué, cuándo (config, aprobaciones, lanzamientos, exportaciones).

### Ingesta: una sola tubería, varios tipos de fuente

Toda fuente (CSV, base de datos, y más adelante API) entrega filas a una tabla de staging. De ahí sigue el mismo camino: mapeo de columnas por tenant (configurable), validación (tipos, duplicados, fechas, huecos, cobertura) y recién después pasa a las canónicas. Los datos de prueba entran por el mismo camino.

Conector de base de datos (incluido en el MVP; implementado en el hito 1 para PostgreSQL y MySQL):
- Conexión de solo lectura a la base del cliente, contra tablas o vistas que el cliente expone.
- El mapeo se define sobre una consulta o vista, no sobre el esquema interno del cliente.
- Sincronización programada e incremental con marca de agua (por fecha de modificación o id creciente), con carga inicial completa.
- Credenciales cifradas y en un gestor de secretos; usuario de solo lectura; lista de IPs permitidas cuando el cliente lo pida. DataPulso no escribe nunca en la base del cliente.
- Conectores por API: después, a demanda, sobre la misma tubería (solo cambia cómo se obtienen las filas).

## 5. Configuración (por tenant, versionada, sin deploy)

1. Vocabulario: cómo se llama cada concepto.
2. Fuentes de datos y mapeo de columnas a las tablas canónicas. (Pendiente: en el hito 1 el mapeo vive en la fuente y no está versionado; versionarlo antes de la pantalla de Configuración.)
3. Etapas activas y su definición como regla sobre eventos (ej.: "activo = al menos 3 eventos de tipo uso en 30 días").
4. Evento objetivo de cada etapa (qué es churn, activación, pago, compra adicional) y ventana de predicción (campo `ventana_prediccion_dias`; si falta, la ventana de medición de la etapa).
5. Ventana de medición de cada acción.
6. Segmentos (reglas sobre atributos) y umbrales de bandas.
7. Catálogo de acciones (sección 7).
8. Política de contacto: tope de contactos por cliente y período, horarios, consentimiento, listas de exclusión.
9. Porcentaje de control por defecto y por acción.
10. Valor económico: valor de un cliente retenido o activado, monto recuperado; costo por contacto. Es obligatorio: sin esto no se calcula ROI. Se carga con un asistente de configuración.
11. Roles y permisos (administrador, analista, aprobador, solo lectura).

Defaults: control 10% (5% en cobranza), bandas por terciles de puntaje, ventana de medición 30 días para activación y 60 a 90 días para retención.

## 6. Plantillas de etapa

| Etapa | Evento objetivo (por defecto) | Señales típicas | Acciones típicas | Métrica de resultado | Ventana |
|---|---|---|---|---|---|
| Alta | Completa el alta | Paso abandonado, tiempo entre pasos, canal | Recordatorio, contacto | % de altas completadas | Días a semanas |
| Activación | Primer uso relevante | Días desde el alta, primer evento | Bienvenida, incentivo, llamado | % que activa | Semanas |
| Uso y valor | Compra o uso adicional | Frecuencia, ticket, productos usados | Oferta, cross-sell | Ingreso adicional | Semanas a meses |
| Retención | Sigue activo a 60 o 90 días | Caída de frecuencia, inactividad, reclamos | Campaña, oferta, llamado | Bajas evitadas | 60 a 90 días |
| Cobranza | Paga en fecha o dentro de N días | Historial de pago, días de atraso, monto | Recordatorio, llamado, acuerdo | Monto recuperado | 30 a 60 días |

Las plantillas son configuración, no código. Un tenant activa las que necesita.

## 7. Acciones

Campos del catálogo: nombre, etapa, tipo [mensaje | oferta o beneficio | tarea humana | lista de llamadas | webhook], canal, plantilla, costo unitario, regla de elegibilidad (banda, segmento, estado), frecuencia máxima, requiere aprobación (sí por defecto, y al inicio sí para todas), impacto esperado (referencia inicial, se reemplaza con lo medido).

Estados de una corrida: borrador, pendiente de aprobación, aprobada, lanzada, medición en curso, cerrada, cancelada.

Regla de seguridad: acciones que cambian algo en el sistema del cliente (bloquear, otorgar crédito, ajustar plan) nunca se ejecutan solas. DataPulso recomienda, el aprobador confirma, y se emite tarea o webhook solo si el cliente lo habilitó.

Recomendación inicial: reglas (banda × segmento → acciones elegibles, ordenadas por impacto esperado y costo). Cuando haya resultados medidos, se reemplaza el impacto esperado por el medido.

## 8. Motor de puntaje

- Modelo simple y explicable por etapa (regresión logística o árboles chicos), con drivers por cliente.
- Qué predice (confirmado el 20/11/2026): puntaje alto = prioridad alta en todas las etapas. Retención: probabilidad de baja en el horizonte. Cobranza: probabilidad de pagar fuera de término algún comprobante que vence en el horizonte. Activación: probabilidad de no activar dentro de los días de la regla. Uso y valor: probabilidad de alcanzar la regla (oportunidad). La etapa Alta no se puntúa mientras los datos no traigan los pasos del alta.
- Horizonte por etapa: la ventana de predicción de la configuración; por defecto, la ventana de medición (en activación y uso y valor, los días de su regla).
- Bandas por terciles de la población puntuada cada día, con la probabilidad siempre al lado de la banda. Con la regla simple no hay probabilidad y se dice explícitamente.
- La calibración (probabilidad predicha contra tasa observada, por banda) se mide en el backtest y se informa junto con la calidad; si no cumple la tolerancia, se avisa que la probabilidad es orientativa.
- Entrenamiento y backtest temporal (entrenar en el pasado, evaluar en el futuro); se muestra la calidad real (por ejemplo, tasa del evento por banda).
- Probabilidades calibradas: "30% de riesgo" debe significar que cerca del 30% efectivamente tiene el evento. El backtest muestra la calibración (probabilidad predicha vs tasa observada por banda), no solo el orden.
- Si los datos no alcanzan (volumen o historia), se usa una regla simple (recencia, frecuencia, monto), marcada como tal en pantalla.
- Se recalcula en un proceso nocturno. Todo puntaje guarda la versión del modelo y de la configuración.

## 9. Medición

1. Al crear la corrida se asigna tratamiento y control de forma aleatoria, estratificada por banda y segmento, con semilla registrada.
2. Control: no se le aplica la acción durante la ventana de medición. El cliente se compromete a eso en el piloto.
3. Resultado: tasa del evento objetivo en tratamiento menos tasa en control, con intervalo de confianza del 95% (prueba de dos proporciones; para montos, diferencia de medias con bootstrap).
4. Valor incremental = diferencia × cantidad de tratados × valor por resultado. ROI = (valor incremental − costo de la acción) / costo.
5. Tamaño mínimo: el producto calcula el efecto mínimo detectable con el tamaño de cada grupo; si no alcanza, el resultado se muestra como "no concluyente" y no se reporta ahorro.
6. Casos sin control posible (acciones obligatorias): se muestra antes y después, marcado como "no causal".
7. Control en cobranza: tamaño chico (5%) o prueba entre dos acciones en lugar de contra "nada". Se decide por acción.
8. Proyectado vs real: al aprobar una corrida se congela la proyección; al cerrar la medición se contrastan (a) lo que el modelo esperaba sin acción contra lo que le pasó al control y (b) el efecto esperado de la acción contra la diferencia medida entre tratados y control, siempre con intervalos de confianza.
9. Un resultado "no concluyente" se muestra, pero no se usa para ajustar ninguna expectativa.
10. Aprendizaje (después del MVP): con resultados concluyentes el producto propone ajustar el impacto esperado de cada acción de forma gradual y una persona aprueba el ajuste; nada se reentrena ni se ajusta solo.

## 10. Pantallas del MVP

- Panorama: KPIs por etapa activa, funnel y segmentos.
- Clientes: lista con puntaje, banda, drivers y acción recomendada; detalle por cliente (con empresa y sus usuarios si hay jerarquía; las pantallas de jerarquía vienen después del modelo).
- Acciones: catálogo, cola de aprobación, corridas e historial, exportación de listas.
- Resultados: por corrida, tratamiento contra control, diferencia con intervalo, ROI y estado (concluyente o no); gráfico de proyectado vs real (sin acción y efecto de la acción) con el desvío de cada uno.
- Configuración: todo lo de la sección 5.
- Datos: fuentes (CSV o base de datos), mapeo, reporte de calidad, historial de cargas y sincronizaciones.

Idioma: solo español en el MVP, con todos los textos en archivos de traducción para sumar inglés sin tocar código.

## 11. Flujo del piloto

1. El cliente da acceso a sus datos: CSV con IDs internos, o una conexión de solo lectura a su base de datos (vistas acordadas). Recomendado: sin nombre, DNI ni mail, o con hash.
2. DataPulso mapea, valida y calcula KPIs y puntajes.
3. Se define con el cliente qué etapa medir primero y qué acciones puede ejecutar él.
4. DataPulso exporta lista de tratamiento y de control; el cliente ejecuta la acción solo a tratamiento.
5. A la ventana de medición, DataPulso toma los datos actualizados (nuevo CSV o sincronización) y calcula el resultado.

Condición del piloto: el cliente respeta el grupo de control.

## 12. Datos de prueba

Generador con semilla que crea una organización completa con efecto de acción conocido. Tres configuraciones de industria: servicios (suscripción/telecom), salud y financiera. Los datos se cargan por la tubería real (CSV y base de datos de prueba) y quedan marcados `is_demo`. Permite repetir la demo con el mismo resultado y probar que el modelo recupera la señal y la medición recupera el efecto.

Estado: el generador del hito 1 produce el historial con la señal de abandono sembrada (usan menos, reclaman más y pagan más tarde antes de irse) y guarda la verdad aparte, fuera de las tablas del producto. El efecto de una acción lo aplicará el simulador de resultados del hito 4.

Salud: los datos de prueba y el producto usan solo eventos administrativos (altas de afiliados, turnos, uso de servicios, pago de cuotas). No se ingieren diagnósticos ni datos clínicos.

## 13. Seguridad y datos personales

- Multi-tenant con aislamiento por fila y por subtenant (cliente del cliente), desde el diseño.
- Cifrado en tránsito y en reposo; accesos auditados; secretos en gestor de secretos.
- Alojamiento en AWS (ya comunicado a prospectos como entorno PCI; confirmar con Marcelo qué tiene hoy).
- Datos personales: el scoring no los necesita; minimizar en la ingesta (IDs internos, hashes).
- Salud: los datos de salud suelen ser datos sensibles; por eso no se ingieren datos clínicos (ver sección 12).
- La adecuación a la ley de protección de datos personales la valida el asesor legal de Diego en cada piloto (esto no reemplaza asesoramiento legal).

## 14. Hitos y criterios de aceptación

Cada hito cierra con un paquete de revisión para Marcelo: código, pruebas, guía de lo que hay que mirar y lista de decisiones tomadas por el agente.

1. Cimientos (construido, pendiente de revisión de Marcelo): esquema multi-tenant, ingesta (CSV y conector de base de datos con sincronización incremental), mapeo y validación, configuración v1, generador de datos de las tres industrias. Aceptación: cargo un CSV o conecto una base de prueba y veo KPIs y funnel calculados desde la base, con el vocabulario del tenant; la segunda sincronización trae solo lo nuevo. Evidencia: 34 pruebas automáticas, todas pasan.
2. Motor (construido, pendiente de revisión de Marcelo; criterio de calibración cumplido parcialmente, tolerancia pendiente de validación por Marcelo): variables, puntaje calibrado, bandas, backtest. Aceptación: lista de clientes con puntaje y drivers; con datos de prueba, el modelo recupera la señal sembrada y las probabilidades están calibradas (predicho vs observado por banda dentro de una tolerancia acordada con Marcelo). Tolerancia (propuesta, a validar por Marcelo): en cada banda, el predicho no se aparta del observado más de 3 puntos porcentuales, o de 2 errores estándar si la banda es chica. Hoy se cumple en 7 de las 8 combinaciones industria × etapa donde se puede medir; Cobranza en servicios no la cumple (5,6 puntos en la banda alta contra una tolerancia de 3,7). Activación no es evaluable con las demos y uso y valor en salud usa la regla simple (ver `docs/REVISION_HITO2.md`). Evidencia: 16 pruebas automáticas; AUC de retención contra la verdad sembrada 0,86 (servicios), 0,77 (salud), 0,82 (financiera); calibración de retención dentro de la tolerancia en las tres industrias.
3. Acciones: catálogo, elegibilidad, recomendación, cola con aprobación, política de contacto, exportación y webhook. Aceptación: apruebo una campaña y obtengo la lista a ejecutar con tratamiento y control; al aprobar queda guardada la proyección congelada, y no se puede modificar después.
4. Medición: resultados, intervalo, ROI, no concluyente. Aceptación: ciclo completo con datos de prueba; el resultado medido coincide con el efecto sembrado dentro del intervalo; el gráfico de proyectado vs real muestra ambos desvíos.
5. Aplicación: shell único y pantallas de la sección 10, en paralelo desde el hito 1 (API con autenticación incluida; hoy solo hay línea de comandos).

Las fechas dependen de la cadencia de revisión de Marcelo (20 hs por semana). Cada hito debe poder revisarse en una sesión corta (la guía del hito 1 estima unas 2 horas).

## 15. Decisiones

Confirmadas por Diego (19/11/2026):
1. Jerarquía empresa-usuarios en el modelo del MVP: sí (pantallas después).
2. Todas las acciones requieren aprobación humana al inicio: sí.
3. Control por defecto 10%, y 5% en cobranza: sí.
4. ROI obligatorio con asistente de configuración: sí.
5. Idioma del MVP solo ES con textos en archivos: sí.
6. Industrias de datos de prueba: servicios, salud y financiera.
7. Vocabulario y nombres de etapas (sección 3): ok.
8. Entrada de datos: no solo CSV; se suma conexión a bases de datos desde el hito 1; APIs más adelante.
9. Primeros motores del conector: PostgreSQL y MySQL.
10. El código se entrega por GitHub (repo `ddinoia/Datapulso`).
11. Marcelo dedica 20 hs por semana.

Confirmadas por Diego (20/11/2026):
12. Proyectado vs real entra en el MVP (hitos 2 a 4). El aprendizaje con ajuste propuesto y aprobado por una persona queda para después del MVP.
13. Qué predice el puntaje de cada etapa: puntaje alto = prioridad alta (ver sección 8).
14. Horizonte de predicción por etapa (`ventana_prediccion_dias`; por defecto la ventana de medición).
15. Bandas por terciles de cada día, mostrando siempre la probabilidad al lado de la banda.

Confirmadas para lo técnico (a revisar por Marcelo):
1. Stack: Postgres, API Python (FastAPI), jobs programados, scikit-learn o LightGBM, React con TypeScript, en AWS.
2. Multi-tenant con RLS y gestión de secretos y accesos.
3. Ingesta con colas para cargas grandes y conector de base de datos.
4. Desarrollo completo por agentes de código; Marcelo revisa después de cada hito.

Abiertas:
- Revisión de Marcelo del hito 1 (ver guía) y sus decisiones sobre los puntos marcados por el agente (por ejemplo, versionar el mapeo, cargas por lotes con reanudación).
- Probar el conector contra MySQL 8 real (se probó con MariaDB 10.11).
- Revisión de Marcelo del hito 2 (ver `docs/REVISION_HITO2.md`).
- Validación por Marcelo de la tolerancia de calibración propuesta por el agente: por banda, |predicho − observado| ≤ máx(3 puntos; 2 errores estándar). Con esa tolerancia, cobranza en servicios hoy no cumple.
