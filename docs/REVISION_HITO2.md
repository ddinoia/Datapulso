# Hito 2 — guía de revisión para Marcelo

Estado: listo para revisar. Construido por un agente de código a partir de `PRODUCTO.md`: sección 8 (motor de puntaje) y criterio de aceptación de la sección 14.
Las tres decisiones de producto del hito (qué predice cada puntaje, horizonte por etapa, bandas por terciles) las **aprobó Diego el 20/11/2026**. Agregó una condición: la probabilidad se muestra siempre al lado de la banda.
Se apoya en el hito 1, pendiente de tu revisión: si cambiás algo del hito 1, avisá y se adapta.
La sección 3 lista lo que el agente decidió por su cuenta.

## 1. Criterio de aceptación y evidencia

> "Lista de clientes con puntaje y drivers; con datos de prueba, el modelo recupera la señal sembrada."

| Qué se pidió | Prueba que lo demuestra (`tests/test_puntaje.py`) |
|---|---|
| Lista de clientes con puntaje, banda y drivers, para todas las etapas puntuables | `test_lista_de_clientes_con_puntaje_banda_y_drivers` |
| La lista usa el vocabulario del tenant ("Afiliados", "el afiliado"), muestra la probabilidad al lado de la banda y la calibración en el resumen del modelo | `test_comando_clientes_muestra_vocabulario_del_tenant` |
| **El modelo recupera la señal sembrada**: puntaje calculado con la base congelada el 02/08/2026, contra las bajas que de verdad ocurrieron en los 90 días siguientes según la verdad del generador (que el producto nunca ve) | `test_puntaje_recupera_la_senal_contra_la_verdad_sembrada` |
| Backtest temporal (entrenar en el pasado, calibrar y evaluar en el futuro) en las 3 industrias. Los coeficientes reproducen la señal: antes de irse usan menos, reclaman más y pagan más tarde. La calibración se mide siempre | `test_backtest_temporal_y_drivers_con_sentido[servicios/salud/financiera]` |
| Calibración: Platt corrige un modelo descalibrado y no se aplica cuando el modelo ya calibra; los drivers siguen sumando el logit final | `test_platt_corrige_un_modelo_descalibrado`, `test_sin_platt_si_el_modelo_ya_calibra` |
| Cobranza aprende de la mora previa | `test_cobranza_usa_la_mora_previa` |
| Sin fuga de información: las variables a una fecha son idénticas con la base completa y con la base congelada en esa fecha | `test_variables_sin_fuga_de_informacion` |
| Regla simple (recencia, frecuencia, monto) cuando los datos no alcanzan, marcada como tal y sin probabilidad | `test_regla_simple_si_los_datos_no_alcanzan` |
| Todo puntaje guarda versión de modelo y de configuración; los modelos son inmutables; mismo dato → mismo modelo | `test_reproducible_y_versionado`, `test_lista_de_clientes_...` |
| Proceso nocturno: reentrena solo si hace falta; una etapa con problemas no frena a las demás | `test_proceso_nocturno_reentrena_solo_cuando_hace_falta`, `test_una_etapa_sin_poblacion_no_frena_el_proceso_nocturno` |
| Aislamiento por tenant (RLS) en las tablas nuevas; todo lo de demo queda `is_demo` | `test_aislamiento_de_puntajes_y_modelos` |
| Comando `datapulso tenants` (nombre, id, industria, demo, versión de config, fecha) | `test_comando_tenants_lista_las_organizaciones` |
| El generador ya no infla la actividad del último ciclo (error encontrado al medir la calibración, ver la sección 3) | `tests/test_kpis_generador.py::test_ciclo_final_sin_actividad_inflada` |

Resultado de la última corrida: **51 pruebas, todas pasan**: 35 en los archivos del hito 1 (34 originales más la del generador) y 16 del hito 2. Tardan ≈ 2 min 40 s con PostgreSQL 16 y MariaDB 10.11 locales.

### Números con las demos de tamaño completo (semilla 7)

Retención, horizonte de 90 días. El AUC mide qué tan bien ordena el puntaje (0,5 = al azar; 1 = perfecto).

| Industria | Clientes evaluados | Tasa de bajas | AUC modelo | AUC regla simple | Bajas en banda alta / media / baja |
|---|---|---|---|---|---|
| Servicios | 1.801 | 10,8 % | **0,862** | 0,699 | 26,3 % / 4,8 % / 1,3 % |
| Salud | 1.695 | 6,7 % | **0,773** | 0,642 | 14,5 % / 3,5 % / 2,1 % |
| Financiera | 1.282 | 8,3 % | **0,816** | 0,714 | 18,9 % / 4,9 % / 0,9 % |

Contra la verdad sembrada (base congelada el 02/08/2026), el AUC es 0,862 / 0,772 / 0,816. La correlación de rangos con el riesgo latente sembrado es 0,52 / 0,40 / 0,49.
El AUC contra la verdad coincide con el del backtest porque la fecha de baja cargada es la sembrada. Es una verificación de consistencia del procedimiento, no una segunda prueba independiente.

Otras etapas (AUC del modelo en el backtest, servicios / salud / financiera):
- Cobranza: 0,67 / 0,62 / 0,65, contra 0,59 / 0,56 / 0,58 de la regla.
- Activación: 0,93 / 0,80 / 0,73, pero con 34 a 121 clientes evaluados. **Esos números son ruidosos.**
- Uso y valor: 0,70 / regla simple (salud no tiene datos suficientes) / 0,67.

Tiempos (servicios: 3.060 entidades, 381.142 eventos):
- Proceso nocturno con reentrenamiento de las 4 etapas: ≈ 49 s.
- Solo recalcular puntajes: ≈ 2,4 s.
- Entrenar retención: ≈ 5 s.

### Calibración: probabilidad predicha contra tasa observada

Se mide en el corte de evaluación del backtest: es fuera de tiempo y ni el modelo ni la calibración lo vieron. La medición es por banda: probabilidad predicha promedio contra tasa observada del evento.

**Tolerancia propuesta**: en cada banda, |predicho − observado| ≤ **máx(3 puntos porcentuales; 2 errores estándar)**.
- Los 3 puntos son el error que importa para decidir.
- El error estándar se calcula con la probabilidad predicha y el tamaño de la banda (√(p(1−p)/n)), bajo la hipótesis "el modelo está calibrado". Así una banda chica no reprueba por ruido.
- Si alguna banda tiene menos de 50 clientes, la medición es **no evaluable**: no se afirma ni que calibra ni que no.

Valores en `puntaje/modelo.py` (`TOLERANCIA_PP`, `MIN_BANDA_CAL`). Es una propuesta: si preferís otra tolerancia, se cambia en un solo lugar.

Resultados (demos de tamaño completo, semilla 7), predicho / observado en % por banda:

| Etapa | Industria | Alta | Media | Baja | Error máximo | Resultado | Ajuste |
|---|---|---|---|---|---|---|---|
| Retención | Servicios | 29,8 / 26,3 | 5,1 / 4,8 | 1,4 / 1,3 | 3,5 pts | cumple (tolerancia 3,7) | ninguno |
| Retención | Salud | 16,0 / 14,5 | 4,9 / 3,5 | 1,9 / 2,1 | 1,5 pts | cumple | ninguno |
| Retención | Financiera | 22,4 / 18,9 | 4,0 / 4,9 | 1,1 / 0,9 | 3,5 pts | cumple (tolerancia 4,0) | ninguno |
| Cobranza | Servicios | 33,0 / 27,3 | 17,7 / 15,1 | 11,4 / 8,9 | 5,6 pts | **no cumple** (tolerancia 3,7) | ninguno |
| Cobranza | Salud | 32,5 / 30,4 | 21,8 / 21,5 | 14,9 / 13,1 | 2,2 pts | cumple | ninguno |
| Cobranza | Financiera | 32,4 / 31,5 | 19,8 / 19,3 | 12,9 / 11,8 | 1,2 pts | cumple | ninguno |
| Uso y valor | Servicios | 57,6 / 67,4 | 42,5 / 50,0 | 20,2 / 25,6 | 9,8 pts | cumple (tolerancia ≈ 10, bandas de 86) | Platt (sin Platt: 7,6 pts) |
| Uso y valor | Financiera | 64,6 / 62,6 | 46,3 / 46,6 | 23,9 / 29,2 | 5,3 pts | cumple (tolerancia ≈ 8) | ninguno |
| Uso y valor | Salud | — | — | — | — | usa la regla simple (sin probabilidad) | — |
| Activación | las 3 | — | — | — | — | no evaluable (bandas de 11 a 41 clientes) | — |

Lectura:
- **Retención calibra dentro de la tolerancia en las tres industrias sin ajuste.** Es la etapa que más importa para los pilotos.
- **Cobranza en servicios no cumple.** La mora del mes evaluado (17,1 %) fue más baja que la de los meses anteriores (alrededor de 21 %). Ninguna calibración ajustada con el pasado anticipa una variación de ese tipo. La probabilidad sobreestima en ese mes, pero el orden (AUC 0,67) se mantiene.
- **Variación entre períodos.** Con poblaciones más chicas, la tasa mensual del evento cambia y eso pesa más que el error del modelo. Ejemplo: financiera con 1.500 clientes (semilla 12), donde la banda alta de retención predijo 19,3 % y se observó 28,6 %. La tasa de bajas del período evaluado (11,7 %) saltó respecto de los cortes de calibración (alrededor de 7,5 %). Con las semillas 13 y 14 cumple. La tolerancia propuesta solo cubre el ruido de muestreo, no esta variación.
- **Platt.** Se ajusta y se aplica solo si el modelo crudo no cumple en los cortes de calibración **y** si mejora fuera de muestra (ajustado con los dos cortes más viejos, tiene que bajar el error en el más reciente). En estas demos pasó esa validación una sola vez, en uso y valor de servicios, y ahí empeoró un poco la evaluación (de 7,6 a 9,8 puntos), aunque sigue dentro de la tolerancia. Conclusión honesta: la regresión logística sin rebalanceo ya sale calibrada con estos datos, y Platt queda como red de seguridad, no como mejora demostrada.
- En pantalla, cuando la calibración no cumple o no es evaluable, el resumen del modelo lo avisa: "tomar la probabilidad como orientativa".

## 2. Cómo correrlo

```bash
pip install -e '.[dev]'                 # suma scikit-learn
scripts/dev_env.sh start                # en Windows: ver README, sección Windows (PowerShell)
datapulso migrar                        # aplica 002_puntaje.sql
datapulso demo salud --via csv
datapulso tenants                                       # nombre, id, industria, demo, versión de config, fecha
datapulso puntuar "<nombre del tenant>"                 # backtest + calibración + entrenamiento + puntaje de las 4 etapas
datapulso modelo "<tenant>" --etapa retencion           # qué predice, método, calidad, calibración y coeficientes
datapulso clientes "<tenant>" --etapa retencion --banda alta --limite 10
datapulso puntuar-todos                                 # lo que correría el programador de tareas cada noche
pytest
```

Ejemplo real (demo de salud):

```
Afiliados · Retención · 2026-10-31 · retencion-v3 (Modelo (regresión logística)) · config v1

  S000326  Alta prioridad · probabilidad 81,3 % · plan: joven
     - Eventos «reclamo» en los últimos 90 días: 4 (promedio 0,2) · sube el puntaje
     - Comprobantes vencidos impagos: 1 (promedio 0,1) · sube el puntaje
     - Antigüedad (días): 635 (promedio 139,8) · sube el puntaje

retencion-v3 · Modelo (regresión logística)
  Predice: probabilidad de que el afiliado se dé de baja en los próximos 90 días
  Calibración (predicho vs observado): Alta prioridad 16 % vs 14,5 % · Media prioridad 4,9 % vs 3,5 % ·
  Baja prioridad 1,9 % vs 2,1 % · error máximo 1,5 puntos · dentro de la tolerancia · sin ajuste
```

## 3. Decisiones

Aprobadas por Diego (20/11/2026):

1. **Qué predice cada puntaje: puntaje alto = prioridad alta.**

   | Etapa | Qué predice | Población |
   |---|---|---|
   | Retención | Probabilidad de **baja** en el horizonte | Vigentes |
   | Cobranza | Probabilidad de **pagar fuera de término** algún comprobante que vence en el horizonte | Vigentes con algún comprobante |
   | Activación | Probabilidad de **no activar** dentro de los días de la regla | Altas recientes que todavía no activaron |
   | Uso y valor | Probabilidad de **alcanzar** la regla de uso y valor (oportunidad) | Vigentes que hoy no la cumplen |

   La etapa Alta no se puntúa porque los datos no traen los pasos del alta.
2. **Horizonte por etapa**: campo opcional `ventana_prediccion_dias`. Si falta, se usa la ventana de medición:
   - retención: 90 días;
   - cobranza: 45 días (servicios y salud) y 30 (financiera);
   - activación y uso y valor: los días de su regla.
3. **Bandas por terciles de la población de cada día, con la probabilidad siempre al lado** ("Alta prioridad · probabilidad 26,5 %"). Con la regla simple no hay probabilidad y se dice explícitamente: "sin probabilidad (regla simple: el puntaje ordena, no estima)".

Tomadas por el agente (para validar), ordenadas por impacto:

4. **Error del generador del hito 1, corregido.**
   - Qué pasaba: los eventos se sorteaban por ciclo de 30 días, y el último ciclo (cortado por la fecha de corte o por la baja) recibía la cantidad de un mes entero en menos días. Los últimos 30 días de la historia tenían alrededor de 43 % más actividad que los meses anteriores.
   - Cómo apareció: al medir la calibración de uso y valor, que subestimaba entre 15 y 33 puntos.
   - Qué se cambió: la tasa se prorratea por los días del ciclo. Es un cambio en `datagen/generador.py`, código del hito 1.
   - Impacto: cambian los datos de prueba (con la misma semilla salen otros datos que antes). Las demos cargadas antes de este cambio hay que regenerarlas para que sus números coincidan con los de esta guía.
5. **Línea de tiempo del backtest con calibración.** Hay tres cortes mensuales de calibración entre el entrenamiento y la evaluación (`motor.py`, docstring):
   - Entrenamiento: hasta 12 cortes mensuales, con etiquetas conocidas antes del primer corte de calibración.
   - Calibración: 3 cortes juntos. Con uno solo, Platt perseguía la variación de un mes y empeoraba: en cobranza de servicios llevaba el error de 6,2 a 9,4 puntos.
   - Evaluación: R − H, siendo R el último día con datos y H el horizonte.

   El modelo de producción repite el esquema corrido H días más adelante. Costo: el entrenamiento usa datos algo más viejos (un horizonte más 60 días), y la historia mínima para usar el modelo es mayor. Por eso uso y valor en salud cayó en la regla simple.
6. **Cuándo se aplica Platt** (ver sección 1). La alternativa es la isotónica: necesita bastante más datos y con poblaciones de cientos de clientes por corte sobreajusta, por eso se eligió Platt.
7. **Cuándo se usa la regla simple.** Se usa cuando falta historia para el backtest completo, o cuando hay menos de 200 clientes distintos, menos de 30 con el evento o menos de 30 sin el evento. Se cuentan clientes, no filas apiladas: con 60 clientes había 700 filas y el motor elegía mal el modelo (bug encontrado y corregido con su prueba). Si en el backtest el modelo ordena peor que la regla, se usa igual el modelo pero queda un aviso.
8. **Variables a fecha de corte, sin fuga** (`puntaje/variables.py`):
   - Eventos por tipo: cantidad en 90 días, tendencia 30 días contra los 60 anteriores, indicador de "ningún evento" y ticket promedio si hay monto.
   - Días sin eventos y antigüedad.
   - Mora: % de comprobantes pagados tarde en 180 días, atraso promedio y comprobantes vencidos impagos.
   - Pertenencia a empresa y atributos categóricos.

   Los **atributos se toman como están hoy** porque la base no guarda su historia; es una fuga posible si un cliente cambia de plan antes de irse. `estado_externo` no se usa nunca.
9. **Variables descubiertas automáticamente.** Se toman los 6 tipos de evento más frecuentes y los atributos de texto con 2 a 12 valores y al menos 50 % de cobertura. No son configurables por el tenant y se guardan con el modelo.
10. **Modelo.**
    - Regresión logística de scikit-learn (C=1, sin rebalanceo de clases) sobre variables estandarizadas.
    - Los totales de eventos se sacaron del modelo porque eran colineales con el detalle por tipo; quedan solo para la regla.
    - Los parámetros, incluida la calibración, se guardan en JSON legible en `modelos.parametros`, sin pickles.
    - LightGBM no se usó: la definición pide explicable y la logística alcanza con estos datos.
11. **Drivers.**
    - Cada driver es la contribución de la variable al logit respecto del cliente promedio del entrenamiento. Si hay Platt, las contribuciones se escalan por su pendiente para que sigan sumando el logit final.
    - Los niveles de un atributo se suman en un solo driver, y lo mismo el indicador "ningún evento" con la cantidad de eventos del mismo tipo.
    - Se guardan los 5 principales y se muestran 3.
    - En la base quedan neutros al idioma; el texto sale de `textos/es.json`. **Los textos del hito 1 siguen en el código.**
12. **Reentrenamiento.** El proceso nocturno reentrena si no hay modelo, si cambió la versión de configuración, si cambió el cálculo de variables (`VERSION_VARIABLES`) o si el modelo tiene más de 30 días. En los demás casos solo recalcula el puntaje, en unos 2,4 s. Los modelos entrenados antes de este cierre no tienen calibración guardada y se siguen pudiendo usar; se recomienda reentrenar con `datapulso puntuar <tenant> --reentrenar`.
13. **Retención sin fecha de baja.** Si el tenant no informa fechas de baja, el evento pasa a ser "queda inactivo según la regla de retención". No se probó con datos reales.
14. **Esquema** (`db/migrations/002_puntaje.sql`):
    - `modelos`: inmutable, con RLS. La app solo puede SELECT/INSERT y además hay un trigger.
    - `puntajes`: con RLS. La app puede SELECT/INSERT/DELETE, porque recalcular el mismo día reemplaza los puntajes de esa fecha.
    - Con RLS no se puede usar COPY: se escribe con `INSERT ... SELECT unnest(...)`.
    - Si dos entrenamientos simultáneos del mismo tenant y etapa chocan, uno falla por clave duplicada. Es aceptable con un proceso nocturno por tenant.
15. **`datapulso tenants`** usa el rol administrador, porque el rol de la app solo ve su propio tenant por RLS. Es una operación de administración, como `migrar`.
16. **Solo personas.** Las empresas no se puntúan, igual que en el funnel del hito 1.

## 4. Qué NO está o no se probó

- **Programación del proceso nocturno**: está el comando `puntuar-todos`, pero no se configuró el programador de tareas (cron o EventBridge en AWS).
- API y pantallas (hito 5). La lista se ve por línea de comandos, y la "acción recomendada" llega en el hito 3.
- **Calibración con datos reales**: solo se midió con el generador, que tiene una señal lineal y limpia. En datos reales hay que esperar AUC más bajos y más variación entre períodos.
- Calibración de activación: no evaluable con estas demos (bandas de 11 a 41 clientes).
- Backtest con varios cortes de evaluación (intervalos de confianza para el AUC y para el error de calibración).
- Rendimiento con más de 400.000 eventos por tenant.
- **Windows**: Diego verificó en Windows el hito 1 (demo por CSV, KPIs y calidad de datos; ver `docs/REVISION_HITO1.md`). Nada del hito 2 se probó en Windows. Los pasos de PowerShell del README los armó el agente sin poder correrlos.
- Siguen pendientes las verificaciones del hito 1: conector contra MySQL 8 real (se probó con MariaDB 10.11), mapeo sin versionar y cargas grandes por lotes.

## 5. Qué mirar primero en la revisión (≈ 2 horas)

1. `src/datapulso/puntaje/variables.py` y `etapas.py`: que ninguna variable ni etiqueta mire el futuro; definición de población y evento por etapa (40 min).
2. `src/datapulso/puntaje/motor.py`: línea de tiempo (entrenamiento, calibración, evaluación), criterio modelo o regla, bandas y proceso nocturno (30 min).
3. `src/datapulso/puntaje/modelo.py`: ajuste, calibración (tolerancia y criterio de Platt), drivers y regla simple (25 min).
4. `src/datapulso/datagen/generador.py`: la corrección del ciclo final (5 min).
5. `db/migrations/002_puntaje.sql`: tablas, RLS y permisos (10 min).
6. Correr `pytest` y `datapulso puntuar` / `clientes` / `modelo` sobre una demo (10 min).
