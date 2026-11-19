# Hito 2 — guía de revisión para Marcelo

Estado: listo para revisar. Construido por un agente de código a partir de `PRODUCTO.md` (v0.3), sección 8 (motor de puntaje) y criterio de aceptación de la sección 14.
Se apoya en el hito 1 (pendiente de tu revisión): si cambiás algo del hito 1, avisá y se adapta.
La sección 3 lista lo que el agente decidió por su cuenta. Las marcadas **[Diego]** son de producto y necesitan su confirmación.

## 1. Criterio de aceptación y evidencia

> "Lista de clientes con puntaje y drivers; con datos de prueba, el modelo recupera la señal sembrada."

| Qué se pidió | Prueba que lo demuestra (`tests/test_puntaje.py`) |
|---|---|
| Lista de clientes con puntaje, banda y drivers, para todas las etapas puntuables | `test_lista_de_clientes_con_puntaje_banda_y_drivers` |
| La lista usa el vocabulario del tenant ("Afiliados", "el afiliado") y los drivers se leen en lenguaje de negocio | `test_comando_clientes_muestra_vocabulario_del_tenant` |
| **El modelo recupera la señal sembrada**: puntaje calculado con la base congelada el 02/08/2026 contra las bajas que de verdad ocurrieron en los 90 días siguientes, según la verdad del generador (que el producto nunca ve) | `test_puntaje_recupera_la_senal_contra_la_verdad_sembrada` |
| Backtest temporal (entrenar en el pasado, evaluar en el futuro) en las 3 industrias; los coeficientes reproducen la señal: usan menos, reclaman más y pagan más tarde antes de irse | `test_backtest_temporal_y_drivers_con_sentido[servicios/salud/financiera]` |
| Cobranza aprende de la mora previa | `test_cobranza_usa_la_mora_previa` |
| Sin fuga de información: las variables a una fecha son idénticas con la base completa y con la base congelada en esa fecha | `test_variables_sin_fuga_de_informacion` |
| Regla simple (recencia, frecuencia, monto) cuando los datos no alcanzan, marcada como tal | `test_regla_simple_si_los_datos_no_alcanzan` |
| Todo puntaje guarda versión de modelo y de configuración; los modelos son inmutables; mismo dato → mismo modelo | `test_reproducible_y_versionado`, `test_lista_de_clientes_...` |
| Proceso nocturno: reentrena solo si hace falta (configuración nueva, modelo viejo, cambio de variables); una etapa con problemas no frena a las demás | `test_proceso_nocturno_reentrena_solo_cuando_hace_falta`, `test_una_etapa_sin_poblacion_no_frena_el_proceso_nocturno` |
| Aislamiento por tenant (RLS) en las tablas nuevas; todo lo de demo queda `is_demo` | `test_aislamiento_de_puntajes_y_modelos` |

Resultado de la última corrida: **47 pruebas, todas pasan** (34 del hito 1 + 13 del hito 2; ≈ 2 min 40 s con PostgreSQL 16 y MariaDB 10.11 locales).

### Números con las demos de tamaño completo (semilla 7)

Retención, horizonte de 90 días. El AUC mide qué tan bien ordena el puntaje (0,5 = al azar; 1 = perfecto). La tasa de bajas se mide en cada banda.

| Industria | Clientes evaluados | Tasa de bajas | AUC modelo | AUC regla simple | Bajas en banda alta / media / baja |
|---|---|---|---|---|---|
| Servicios | 1.801 | 10,8 % | **0,853** | 0,696 | 25,3 % / 4,8 % / 2,3 % |
| Salud | 1.695 | 6,7 % | **0,761** | 0,632 | 14,2 % / 3,2 % / 2,8 % |
| Financiera | 1.282 | 8,3 % | **0,807** | 0,717 | 17,8 % / 5,9 % / 1,2 % |

Contra la verdad sembrada (base congelada el 02/08/2026): AUC 0,853 / 0,761 / 0,807. La correlación de rangos con el riesgo latente sembrado es 0,54 / 0,40 / 0,50.
El AUC contra la verdad coincide con el del backtest porque la fecha de baja cargada es la sembrada. Es una verificación de consistencia del procedimiento, no una segunda prueba independiente del modelo.

Otras etapas (AUC del modelo en el backtest, servicios / salud / financiera):
- Cobranza: 0,64 / 0,64 / 0,63, contra 0,56 / 0,58 / 0,57 de la regla.
- Activación: 0,90 / 0,74 / 0,89, pero con apenas 36 a 120 clientes evaluados. **Esos números son ruidosos.**
- Uso y valor: 0,73 / 0,78 / 0,72.

Cobranza es más débil porque la mora sembrada es muy aleatoria comprobante por comprobante. Igual ordena mejor que la regla.

Tiempos (servicios: 3.060 entidades, 398.766 eventos):
- Proceso nocturno con reentrenamiento de las 4 etapas: ≈ 46 s.
- Solo recalcular puntajes: ≈ 3 s.
- Entrenar retención: ≈ 6 s.

## 2. Cómo correrlo

```bash
pip install -e '.[dev]'                 # suma scikit-learn
scripts/dev_env.sh start
datapulso migrar                        # aplica 002_puntaje.sql
datapulso demo salud --via csv
datapulso puntuar "<nombre del tenant>"                 # backtest + entrenamiento + puntaje de las 4 etapas
datapulso modelo "<tenant>" --etapa retencion           # qué predice, método, calidad del backtest, coeficientes
datapulso clientes "<tenant>" --etapa retencion --banda alta --limite 10
datapulso puntuar-todos                                 # lo que correría el programador de tareas cada noche
pytest
```

Ejemplo real de `clientes` (demo de salud):

```
Afiliados · Retención · 2026-10-31 · retencion-v2 (Modelo (regresión logística)) · config v1

  S002092  Puntaje 0.830  Alta prioridad · plan: joven
     - Eventos «reclamo» en los últimos 90 días: 2 (promedio 0,2) · sube el puntaje
     - Comprobantes pagados tarde o impagos (últimos 180 días): 100 % (promedio 15 %) · sube el puntaje
     - Comprobantes vencidos impagos: 1 (promedio 0,1) · sube el puntaje
```

## 3. Decisiones que tomó el agente (para validar)

Ordenadas por impacto si te parecen mal:

1. **[Diego] Qué predice cada puntaje.** La definición dice "probabilidad del evento objetivo", pero para retención el evento objetivo es "sigue activo", y eso daría prioridad alta a quien menos la necesita. Se eligió que **puntaje alto = prioridad alta** en todas las etapas:

   | Etapa | Qué predice | Población |
   |---|---|---|
   | Retención | Probabilidad de **baja** en el horizonte | Vigentes |
   | Cobranza | Probabilidad de **pagar fuera de término** algún comprobante que vence en el horizonte | Vigentes con algún comprobante |
   | Activación | Probabilidad de **no activar** dentro de los días de la regla | Altas recientes que todavía no activaron |
   | Uso y valor | Probabilidad de **alcanzar** la regla de uso y valor (es una oportunidad, no un riesgo) | Vigentes que hoy no la cumplen |

   La etapa Alta no se puntúa porque los datos no traen los pasos del alta.
2. **[Diego] Horizonte del puntaje.** Se agregó a la configuración un campo opcional, `ventana_prediccion_dias`, por etapa. Si falta, se usa la ventana de medición de la etapa. Así el puntaje predice lo mismo que después se mide:
   - retención: 90 días en las 3 plantillas;
   - cobranza: 45 días (servicios y salud) y 30 (financiera);
   - activación y uso y valor: los días de su regla.

   Es un campo nuevo del modelo de configuración. No hizo falta crear versiones nuevas: las existentes siguen siendo válidas.
3. **[Diego] Bandas por terciles de la población de cada día.** Siempre hay un tercio en cada banda. Eso sirve para planificar capacidad, pero "alta" no significa una probabilidad absoluta alta: en salud la banda alta tiene 14 % de bajas. La alternativa es fijar umbrales de probabilidad por etapa.
4. **Cuándo se usa la regla simple.** Se usa cuando falta historia para un backtest o cuando hay menos de 200 clientes distintos, menos de 30 con el evento o menos de 30 sin el evento. Se cuentan clientes, no filas apiladas (ver punto 5): con 60 clientes había 700 filas y el motor elegía mal el modelo (bug encontrado y corregido con su prueba). Si en el backtest el modelo ordena peor que la regla, se usa igual el modelo pero queda un aviso. La alternativa es usar lo que mejor anda.
5. **Diseño del backtest.**
   - Un solo corte de evaluación (R − H, siendo R el último día con datos y H el horizonte).
   - Hasta 12 cortes de entrenamiento mensuales, con etiquetas conocidas antes de la evaluación. El mismo cliente aparece en varios cortes.
   - El modelo de producción se reentrena H días más adelante, con lo más reciente. La calidad informada es la del backtest, no la del modelo final.
   - No hay validación con varios cortes de evaluación, así que el AUC de una sola corrida tiene varianza: sobre todo en activación, con 36 a 120 clientes evaluados.
6. **Variables a fecha de corte, sin fuga** (`puntaje/variables.py`):
   - Eventos por tipo: cantidad en 90 días, tendencia 30 días contra los 60 anteriores, indicador de "ningún evento" y ticket promedio si hay monto.
   - Días sin eventos y antigüedad.
   - Mora: % de comprobantes pagados tarde en 180 días, atraso promedio y comprobantes vencidos impagos.
   - Pertenencia a empresa y atributos categóricos.

   Los **atributos se toman como están hoy**, porque la base no guarda su historia: si un cliente cambia de plan antes de irse, eso filtra información. `estado_externo` no se usa nunca.
7. **Variables descubiertas automáticamente.** Se toman los 6 tipos de evento más frecuentes y los atributos de texto con 2 a 12 valores y al menos 50 % de cobertura. No son configurables por el tenant. Se guardan con el modelo, para que el puntaje de mañana use las mismas.
8. **Modelo.**
   - Regresión logística de scikit-learn (C=1, sin rebalanceo de clases, para que las probabilidades queden en escala real) sobre variables estandarizadas.
   - Los totales de eventos se sacaron del modelo: eran colineales con el detalle por tipo y producían drivers contradictorios. Quedan solo para la regla.
   - Los parámetros se guardan en JSON legible en `modelos.parametros`, sin pickles: un puntaje se puede recalcular a mano.
   - LightGBM no se usó: la definición pide explicable y la logística alcanza con estos datos.
9. **Drivers.**
   - Cada driver es la contribución de la variable al logit respecto del cliente promedio del entrenamiento.
   - Los niveles de un atributo se suman en un solo driver ("Plan: joven"), y lo mismo el indicador "ningún evento" con la cantidad de eventos del mismo tipo, para no mostrar dos drivers que se contradicen.
   - Se guardan los 5 principales y se muestran 3.
   - En la base quedan neutros al idioma; el texto se arma al mostrarlos, desde `textos/es.json`. Es el primer archivo de textos del producto: **los textos del hito 1 siguen en el código**.
10. **Reentrenamiento.** El proceso nocturno reentrena si no hay modelo, si cambió la versión de configuración, si cambió el cálculo de variables (`VERSION_VARIABLES`) o si el modelo tiene más de 30 días. En los demás casos solo recalcula el puntaje, en unos 3 s. Recalcular el mismo día reemplaza los puntajes de esa fecha y etapa; los de otras fechas quedan como historial.
11. **Retención sin fecha de baja.** Si el tenant no informa fechas de baja, el evento pasa a ser "queda inactivo según la regla de retención". No se probó con datos reales.
12. **Esquema** (`db/migrations/002_puntaje.sql`):
    - `modelos`: inmutable, con RLS. La app solo puede SELECT/INSERT y además hay un trigger.
    - `puntajes`: con RLS. La app puede SELECT/INSERT/DELETE, porque recalcular el mismo día reemplaza los puntajes de esa fecha.
    - Con RLS no se puede usar COPY, así que la escritura es un único `INSERT ... SELECT unnest(...)`.
    - Si dos entrenamientos del mismo tenant y etapa corren a la vez, uno falla por clave duplicada. Es aceptable con un proceso nocturno por tenant.
13. **Solo personas.** Las empresas no se puntúan, igual que en el funnel del hito 1.

## 4. Qué NO está (a propósito o por límite del entorno)

- **Programación del proceso nocturno**: está el comando `puntuar-todos`, pero no se configuró el programador de tareas (cron o EventBridge en AWS).
- API y pantallas (hito 5). La lista de clientes se ve por línea de comandos. Por eso todavía no está la "acción recomendada" en la lista: llega en el hito 3.
- Calibración de probabilidades: no se midió si un 30 % predicho es un 30 % real. La logística sin rebalanceo suele quedar razonable, pero no está probado.
- Backtest con varios cortes de evaluación (intervalos de confianza para el AUC).
- Pruebas con datos reales de cliente. Todo se validó con el generador, cuya señal es lineal y limpia: en datos reales hay que esperar AUC más bajos.
- Rendimiento con más de 400.000 eventos por tenant: no se probó.
- Siguen pendientes las verificaciones del hito 1: `docker-compose.yml` sin probar, conector contra MySQL 8 real (se probó con MariaDB 10.11), mapeo sin versionar y cargas grandes por lotes.

## 5. Qué mirar primero en la revisión (≈ 2 horas)

1. `src/datapulso/puntaje/variables.py` y `etapas.py`: que ninguna variable ni etiqueta mire el futuro; definición de población y evento por etapa (45 min).
2. `src/datapulso/puntaje/motor.py`: cortes del backtest, criterio modelo o regla, bandas y proceso nocturno (30 min).
3. `src/datapulso/puntaje/modelo.py`: ajuste, drivers y regla simple (20 min).
4. `db/migrations/002_puntaje.sql`: tablas, RLS y permisos (10 min).
5. Correr `pytest` y `datapulso puntuar` / `clientes` / `modelo` sobre una demo (15 min).
