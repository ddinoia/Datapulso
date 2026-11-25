# Hito 3 — guía de revisión para Marcelo

Estado: listo para revisar. Construido por un agente de código a partir de `PRODUCTO.md` v0.4 (secciones 4, 5, 7, 9 y 14) y de las decisiones de producto D1 a D9 de Diego (24/11/2026).
Se apoya en los hitos 1 y 2, ambos pendientes de tu revisión. El hito 4 (simulador del efecto y medición) **no se empezó**.
La sección 3 lista lo que el agente decidió por su cuenta. Las marcadas **[pendiente]** necesitan tu validación o la de Diego.

## 1. Criterio de aceptación y evidencia

> "Apruebo una campaña y obtengo la lista a ejecutar con tratamiento y control; al aprobar queda guardada la proyección congelada, y no se puede modificar después." (`PRODUCTO.md` v0.4, sección 14)

**Estado del criterio: cumple** con los datos de prueba, por línea de comandos (todavía no hay pantallas ni API).

| Qué se pidió | Prueba que lo demuestra (`tests/test_acciones.py`) |
|---|---|
| Ciclo completo sobre servicios (retención): recomendar, crear, pedir aprobación, aprobar, exportar y lanzar, con auditoría de cada paso | `test_ciclo_completo_retencion_servicios` |
| Una corrida aprobada no puede modificar proyección, lista ni grupos: ni con el rol de la app, ni con el rol dueño de las tablas, ni cambiando después la configuración o el catálogo | `test_corrida_aprobada_no_se_puede_modificar` |
| Sin aprobación no hay exportación, webhook ni lanzamiento | `test_sin_aprobacion_no_hay_exportacion_ni_webhook_ni_lanzamiento`, `test_webhook_firmado_idempotente_con_reintentos` |
| Sin valor económico no se aprueba, y el mensaje dice qué falta. Cargarlo no reentrena modelos | `test_sin_valor_economico_no_se_aprueba` |
| Asignación reproducible con la misma semilla; control exacto de 10 % y 5 % (cobranza); balance por banda y segmento | `test_asignacion_reproducible_y_balanceada`, `test_asignacion_en_una_corrida_real`, `test_sin_valor_economico_no_se_aprueba` (5 %) |
| Política de contacto: consentimiento, lista de exclusión y tope de contactos contando corridas anteriores (el control no cuenta). Un cliente no entra en dos corridas abiertas de la misma etapa, ni por el código ni insertando directo en la base | `test_politica_de_contacto` |
| La proyección coincide con el cálculo manual de D2 en un caso chico (40 clientes), incluido el efecto mínimo detectable | `test_proyeccion_coincide_con_calculo_manual` |
| Recomendación ordenada por ROI esperado por cliente, con el motivo y la elegibilidad por banda; las acciones que cambian el sistema del cliente se marcan | `test_recomendacion_ordenada_por_roi_y_explicable` |
| Roles, cuatro ojos y puntajes de más de 7 días | `test_permisos_cuatro_ojos_y_puntajes_viejos` |
| RLS: un tenant no ve ni escribe usuarios, acciones, corridas, listas, salidas ni entregas de otro | `test_aislamiento_de_acciones_corridas_y_entregas` |
| Webhook: firma HMAC válida, misma clave de idempotencia en el reintento, reintento ante 503, sin reintento ante 401, solo viaja el tratamiento, reemitir no duplica, JSON válido | `test_webhook_firmado_idempotente_con_reintentos`, `test_webhook_4xx_no_se_reintenta` |
| Plantillas de salud solo administrativas; impacto rotulado como supuesto | `test_plantillas_de_salud_solo_administrativas_e_impacto_supuesto` |

Resultado de la última corrida: **65 pruebas, todas pasan**: 35 en archivos del hito 1, 16 del hito 2 y 14 del hito 3. Tardan ≈ 4 min con PostgreSQL 16 y MariaDB 10.11 locales.

### Rendimiento

Demo de servicios de tamaño completo (3.060 entidades, 381.142 eventos), medido por el agente en Linux:

| Acción | Clientes en la corrida | Crear (elegibilidad, política, asignación, proyección, escritura) | Aprobar (proyección congelada) |
|---|---|---|---|
| `RET_LLAMADA` (banda alta) | 636 | 0,13 s | — |
| `RET_MENSAJE` (bandas alta y media) | 620 | 0,19 s | 0,02 s |
| `COB_RECORDATORIO` (todas las bandas) | 1.891 | 0,26 s | 0,04 s |

En la prueba de aceptación (1.500 clientes) crear la corrida tarda menos de 0,2 s. El tope que verifica la prueba es 10 s.

### Ejemplo real (demo de salud)

```
C-1 · Borrador · etapa retencion · semilla 7 · control 10 %
  Puntuados 1987 · elegibles para la acción 663 · excluidos por la política 52
     - 52 no dio consentimiento de contacto
  Efecto mínimo detectable ≈ 13,2 puntos · efecto esperado 1,7 puntos
  Aviso: Con 550 tratados y 61 de control el efecto mínimo detectable es ≈ 13,2 puntos y el esperado es
  1,7 puntos: es probable que el resultado sea "no concluyente"
  Proyección CONGELADA 2026-11-25T15:11:57
    Acción RET_ATENCION_AFILIADO v1 · impacto 12 % (supuesto, no medido) · modelo retencion-v1 · config v2
    Tratamiento 550 · control 61 · tasa esperada sin acción 14,2 % (tratamiento 14,3 %, control 13 %)
    Efecto esperado 1,7 puntos (rango 0,9 puntos a 2,6 puntos) · eventos evitados 9,4 (4,7 a 14,2)
    Valor esperado $ 849.702 · costo $ 825.000 · ROI esperado 3 % (rango −48,5 % a 54,5 %)
    Calibración del modelo en el backtest: error máximo 1,5 puntos · dentro de la tolerancia
```

El aviso de "no concluyente" no es un error: con 10 % de control sobre unos 600 clientes, el control es demasiado chico para detectar un efecto de 2 puntos. Es exactamente lo que D5 pide advertir antes de aprobar.

## 2. Cómo correrlo

```bash
datapulso migrar                                         # aplica 003_acciones.sql
datapulso demo servicios --via csv
datapulso tenants
T="<nombre de la organización>"
datapulso puntuar "$T" --etapa retencion
datapulso usuario-alta "$T" admin administrador          # el primero no pide --usuario (bootstrap)
datapulso usuario-alta "$T" ana analista --usuario admin
datapulso usuario-alta "$T" apro aprobador --usuario admin
datapulso catalogo "$T" --cargar-plantilla --usuario admin
datapulso valor-economico "$T" --etapa retencion --valor 60000 --usuario admin
datapulso recomendar "$T" --etapa retencion --limite 3
datapulso corrida-crear "$T" --accion RET_LLAMADA --usuario ana
datapulso corrida-pedir-aprobacion "$T" 1 --usuario ana
datapulso cola-aprobacion "$T"
datapulso corrida-aprobar "$T" 1 --usuario apro
datapulso corrida-exportar "$T" 1 --usuario ana --carpeta out/exportaciones
datapulso corrida-lanzar "$T" 1 --usuario ana --fecha 2026-11-25
datapulso corridas "$T"
datapulso corrida "$T" 1                                 # diseño, exclusiones y proyección
# webhook (acciones de tipo tarea humana o webhook):
datapulso salida-webhook "$T" crm https://crm.ejemplo.com/datapulso --usuario admin
datapulso corrida-emitir "$T" 2 --usuario apro
```

## 3. Decisiones del agente (para validar), ordenadas por impacto

1. **[pendiente] El catálogo vive en una tabla propia con versiones inmutables, no en `config_versions`.** La sección 5, punto 7, lo pone dentro de la configuración. Se separó por tres motivos:
   - el catálogo cambia más seguido (costos, impacto, textos);
   - cada corrida tiene que apuntar a la versión exacta de la acción aprobada;
   - con el comportamiento anterior, cada cambio de configuración reentrenaba todos los modelos.

   Se mantiene el principio: nunca se edita una fila, se inserta la versión siguiente (trigger igual al de `config_versions`).
2. **[pendiente] Cambio en el hito 2: el proceso nocturno ya no reentrena por cualquier versión nueva de configuración.** Compara una huella de la parte que afecta a cada modelo: regla y horizonte de la etapa, gracia de cobranza y atributos excluidos (`puntaje/motor.py::huella_config`). Sin esto, cargar el valor económico (obligatorio para aprobar) reentrenaba todo y cambiaba los puntajes en medio de una aprobación. Los puntajes siguen guardando la versión de configuración vigente.
3. **[pendiente, Diego] Consentimiento de contacto.**
   - Se lee de un atributo de la entidad (`politica_contacto.atributo_consentimiento`, por defecto `consentimiento_contacto`; sí = si, sí, s, true, 1).
   - Con `consentimiento_requerido = true` (el valor por defecto), **un cliente sin el dato queda afuera**. Con datos reales, el piloto tiene que mapear ese atributo o desactivar el requisito.
   - El generador ahora produce el atributo (92 % "si", con un generador aleatorio aparte, así no cambian los demás datos de la misma semilla).
   - El atributo se excluye de las variables del modelo: es un permiso, no un comportamiento.
4. **[pendiente, Diego] Rango del efecto esperado: impacto × (1 ± 50 %)** (`acciones.rango_impacto`). Como el impacto es un supuesto, el rango refleja esa incertidumbre. No incluye el error de calibración del modelo, que se muestra aparte.
5. **[pendiente, Diego] Valor por resultado en cobranza.** Se usa el valor configurado por resultado, no la deuda de cada cliente. Usar la deuda real haría el ROI de cobranza más preciso, pero no está en D2.
6. **[pendiente, Diego] Roles.**

   | Rol | Puede |
   |---|---|
   | Administrador | Todo |
   | Analista | Crear, pedir aprobación, exportar y lanzar; cancelar solo las suyas antes de aprobar |
   | Aprobador | Aprobar, cancelar, exportar, lanzar y emitir webhooks |
   | Solo lectura | Consultar |

   El primer usuario de cada organización se crea sin `--usuario` y tiene que ser administrador. **`--usuario` no es autenticación**: cualquiera con acceso a la línea de comandos puede poner cualquier nombre. La autenticación llega con la API en el hito 5.
7. **Qué se congela y cuándo.**
   - La lista y los grupos solo se escriben en borrador. Desde "pendiente de aprobación" quedan fijos, para que el aprobador apruebe lo que vio.
   - La proyección preliminar se calcula al crear; al aprobar se recalcula sobre esa lista con la configuración vigente (por ejemplo, un valor económico cargado después) y se congela.
   - Desde la aprobación, un trigger rechaza cualquier cambio de proyección, diseño, semilla, acción o versiones, y cualquier escritura en la lista.
   - Las corridas no se borran (ni con el rol dueño): se cancelan con motivo.
8. **Estados.**
   - La transición de "pendiente" a "borrador" existe en la base, pero no hay comando para devolver: para rechazar, se cancela con motivo.
   - Una corrida lanzada no se puede cancelar: rompería la medición, y qué hacer en ese caso queda para el hito 4.
   - "Medición en curso" y "cerrada" existen en la base y en las transiciones, pero los maneja el hito 4.
9. **Puntajes viejos (D4).** La antigüedad se mide desde que se calcularon (`puntajes.creado_en`), no desde la fecha de los datos: la demo tiene datos al 31/10 y quedaría siempre vieja. Más de 7 días (configurable) impide crear corridas y aprobar las pendientes.
10. **Sin probabilidad no hay corrida.** Si una etapa usa la regla simple (sin probabilidad), no se pueden crear corridas: no hay con qué proyectar. Eso pasa hoy con activación en demos chicas y con uso y valor en salud.
11. **Uso y valor no tiene acciones.** D1 define el impacto sobre eventos adversos (baja, mora, no activación); uso y valor es una oportunidad. El catálogo acepta solo retención, cobranza y activación. Definir el impacto de una oportunidad queda pendiente.
12. **Política de contacto (D6).**
    - El orden de los motivos es: corrida abierta, lista de exclusión, sin consentimiento, frecuencia máxima de la misma acción, tope de contactos.
    - Cada cliente excluido queda en `corrida_excluidos` con su primer motivo.
    - La fecha de contacto de una corrida es la de lanzamiento, o la de aprobación si todavía no se lanzó.
    - El tope cuenta los tratamientos de cualquier etapa en los últimos 30 días.
    - Una corrida en borrador también cuenta como abierta: un borrador abandonado bloquea a sus clientes hasta que se cancela.
13. **`--limite`** toma los N clientes de mayor probabilidad que quedan después de la política.
14. **Asignación (D5).**
    - El control total es exactamente round(N × %).
    - Por estrato (banda × segmento) se asigna la parte entera de la cuota, y los clientes que faltan van a los estratos con mayor resto. Así ningún estrato se aparta más de 1 cliente de su cuota exacta: esa es la tolerancia de balance que se prueba.
    - La permutación usa la semilla registrada, sobre los clientes ordenados por id.
15. **Efecto mínimo detectable.** Es la fórmula aproximada de dos proporciones (95 % a dos colas, 80 % de potencia) con la tasa esperada promedio. Es una advertencia previa; el cálculo de resultados es del hito 4.
16. **Exportación (D9).**
    - Se exportan tratamiento y control, para que el cliente sepa a quién no contactar.
    - El hash va en un archivo `.sha256` al lado de cada CSV y en la auditoría.
    - El "motivo principal" es el driver del puntaje (por ejemplo, "Eventos «turno» en los últimos 90 días: 0"). No identifica a nadie, pero es información de comportamiento ligada a un id: conviene que el asesor legal lo mire en el primer piloto.
17. **Webhook (D9).**
    - Solo salen las acciones de tipo tarea humana o webhook, y solo el grupo de tratamiento, en lotes de 500.
    - Hasta 3 intentos con espera exponencial ante errores de red, 408, 429 y 5xx; otro 4xx no se reintenta.
    - Se exige https salvo para localhost (pruebas).
    - El secreto se muestra una sola vez y se guarda cifrado como las credenciales de fuentes.
    - La clave de idempotencia sale de corrida + salida + lote + hash del cuerpo. Un lote ya entregado no se vuelve a mandar, y el receptor puede deduplicar los reintentos.
    - Las acciones que cambian el sistema del cliente viajan con `requiere_confirmacion_humana: true`.
    - La emisión es siempre un comando explícito de un aprobador o administrador: aprobar no emite nada.
18. **Las plantillas de acciones son datos del tenant**, no textos de producto. Viven en `acciones/plantillas.py`; los textos de pantalla del hito 3 están en `textos/es.json`.
19. **Concurrencia.**
    - El número de corrida por tenant y la regla de "una corrida abierta por etapa" se controlan en la base (trigger), pero sin bloqueo explícito.
    - Dos personas creando corridas al mismo tiempo podrían chocar: una falla con error, no se corrompe nada.
    - No se probó con usuarios concurrentes.

## 4. Qué NO está o no se probó

- **Hito 4**: simulador del efecto sembrado, medición, resultados, desvíos proyectado contra real y gráfico. No se empezó.
- **Autenticación, API y pantallas** (hito 5). Hoy todo es línea de comandos, y `--usuario` es un dato declarado, no una identidad verificada.
- **Webhook contra un sistema real**: se probó solo con un receptor local por http en la misma máquina. No hay rotación de secretos ni cola asíncrona: la emisión corre dentro del comando y reintenta en el momento.
- **Corridas de activación**: el catálogo tiene acciones de activación, pero con las demos el puntaje de activación suele usar la regla simple (pocos clientes por corte), y entonces no se puede crear la corrida. No hay prueba de una corrida de activación.
- **Consentimiento con datos reales**: solo con el atributo sembrado por el generador.
- **Usuarios concurrentes**: no se probaron.
- **Windows**: nada del hito 3 se probó en Windows. La sección de Windows del README sigue cubriendo solo los hitos 1 y 2.
- Siguen pendientes las verificaciones anteriores:
  - hito 1: MySQL 8 real, mapeo sin versionar, cargas grandes por lotes;
  - hito 2: tolerancia de calibración, con cobranza en servicios fuera de ella.

## 5. Qué mirar primero en la revisión (≈ 2 horas)

1. `db/migrations/003_acciones.sql`: triggers de inmutabilidad y transiciones, conflicto de corridas abiertas, RLS y permisos (35 min).
2. `src/datapulso/acciones/corridas.py`: asignación, efecto mínimo detectable, proyección (D2), aprobación (35 min).
3. `src/datapulso/acciones/politica.py` y `recomendacion.py`: orden de los motivos y cálculo del ROI por cliente (20 min).
4. `src/datapulso/acciones/webhook.py`: firma, idempotencia, reintentos (15 min).
5. `src/datapulso/puntaje/motor.py::huella_config`: el cambio de reentrenamiento del hito 2 (5 min).
6. Correr `pytest` y el ciclo de la sección 2 sobre una demo (10 min).
