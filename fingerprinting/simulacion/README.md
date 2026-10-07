# Simulación del planificador

## Experimento posterior: propagar dentro de cada slice

Disponible mediante history_aggregation=per_slice. Cada lectura normalizada d
aporta 1-2*d únicamente en la slice de su intento; fallo d=1 aporta -1,
desconocido NaN no aporta evidencia. Se propaga por exp(-distancia_xy/.30)
dentro de esa slice y después se suman las slices con pesos 0.9^edad,
dividiendo por la suma de pesos de W. No se escribe en otras celdas de M.

20 semillas x 900 pasos x 2 personas: data 54.0667% / 53.0778%, alternancia
91.2959% / 91.4071%, data al coincidir 51.0979% / 50.5485%, cero colisiones.
Los archivos resultados_multi.json y multi.html se regeneran con la configuración
actual: tras la revisión final vuelven a mostrar per_anchor, con 62.3778% /
62.0056% de data y cero colisiones en 36000 intentos.
La configuración de hardware conserva per_anchor (62.3778% / 62.0056% data),
pues esta prueba empeora los resultados. replay.html conserva el experimento
individual con per_anchor. Pasan 33 pruebas, incluida la ausencia de cambios
en slices anteriores/futuras y la simetría del premio y penalización.

## Actualización: distancias normalizadas e influencia exponencial recuperada

El modelo actual guarda muestras y M en [0,1]: D_Max=1, fallo=1,
desconocido=NaN/null. La entrada del hardware sigue siendo cm y se convierte
una vez; la simulación entrega valores normalizados directamente. X,Y están
en [0,1] con una unidad de eje equivalente a D_Max metros. La influencia
espacial vuelve a `exp(-distancia_xy / neighbor_scale)`, con neighbor_scale=0.30,
tanto para premio como penalización. La sigmoide final y la alternancia se conservan.
Los campos distance y measurement de los recorridos son normalizados;
los campos explícitos *_m conservan diagnósticos en metros.

La prueba descartada de influencia lineal, con 20 semillas y 900 pasos:
un reloj obtuvo 57.7167% de data y
89.4105% de alternancia. Dos relojes obtienen 56.4333% y 55.9389% de data,
con 89.9944% y 89.5940% de alternancia y cero colisiones en 36000 intentos.
La propagación lineal era más amplia y reducía data frente a la exponencial
anterior (63.8222% en uno y aproximadamente 62% en dos). Se registra esta
regresión. Se recupera la exponencial manteniendo M y las entradas de simulación
normalizadas; no se modifican pesos, recorridos ni radio de cobertura.

Repetición confirmada de la exponencial normalizada: persona 1, 62.3778% data,
87.4583% alternancia y 60.1574% data al coincidir; persona 2, 62.0056% data,
87.3693% alternancia y 59.8908% data al coincidir. Cero colisiones en 36000
intentos; 29 pruebas unitarias y de protocolo pasan.

Los apartados siguientes conservan el historial de experimentos anteriores;
sus métricas describen sus respectivos experimentos. Los JSON/HTML generados
se han actualizado. En hardware debe calibrarse la relación entre los clicks
de sit_placas y metros: el CSV no incluye dimensiones físicas del plano.

## Prueba con dos personas y placas exclusivas

```powershell
python fingerprinting/simular_multiocupacion.py
```

20 semillas, 900 pasos, dos relojes, nueve placas, parejas de 1.5 m, radio
4.11 m, W=15 y w_cercania=w_equidad=1. Una medida por reloj y paso. Las rutas
alternan bloques de destino compartido y separado de 150 pasos, con movimiento
continuo y ruido pequeño. Coinciden en un núcleo el 43.41% de los pasos.

`choose_batch` reserva cada placa elegida dentro del lote antes de evaluar al
siguiente reloj. Se rota el primero en cada paso: cada reloj abre 450 veces.
Las placas ocupadas se excluyen y, si no quedan libres, se devuelve None,
nunca una placa duplicada. El llamador debe serializar lotes y conservar las
reservas hasta que terminen las sesiones. En esta simulación acaban cada paso.
El orquestador real ya excluye sesiones activas y verifica la reserva bajo
lock al iniciar una sesión; esta prueba no añade latencia de hardware.

| Política / reloj | Con distancia | Alternancia | Con distancia al coincidir |
| --- | ---: | ---: | ---: |
| Heurística / reloj 1 | 62.38% | 87.46% | 60.16% |
| Heurística / reloj 2 | 62.01% | 87.37% | 59.89% |
| Uniforme exclusiva / reloj 1 | 36.12% | 88.80% | 33.14% |
| Uniforme exclusiva / reloj 2 | 35.69% | 88.85% | 33.11% |

Cero asignaciones duplicadas en los 18000 pasos conjuntos (36000 asignaciones)
de cada política. Se comprueba la exclusividad en cada paso, una celda finita
por reloj y slice, y W=15. Prueba unitaria adversarial: 1000 lotes con ambos
relojes prefiriendo la misma placa, reservas externas y escasez de placas.
Los porcentajes condicionados a coincidencia son medias por recorrido.
Resultados: resultados_multi.json y multi.html; el experimento de un reloj
se conserva en resultados.json y replay.html.

## Prueba de un reloj

Configuración activa: `w_cercania=1` y `w_equidad=1`, con alternancia suave.
Los fallos se representan como `D_Max` en M, sin memoria adicional de fallos.
Los resultados guardados corresponden al modelo corregido: una selección y
una sola medida por paso, W=15, 20 recorridos de 900 pasos (18000 intentos).
Cada paso añade una slice; las otras placas quedan en NaN. Los NaN se excluyen
de las medias ponderadas; una distancia real cero se conserva. El snapshot
JSON usa null para las desconocidas. El fallo es D_Max, no desconocido.

## Experimento actual: una medida y una selección por paso

```powershell
python fingerprinting/simular_asignacion.py --layout double --movement cores --pair-spacing-m 1.5
```

Se mantienen los mismos movimientos, posiciones y radio 4.11 m: estancias
de 60 a 120 pasos en cada núcleo y viajes a otros núcleos. Decaimiento 0.9,
sigmoide de pendiente 4 y w_cercania=w_equidad=1. Sin latencia de hardware.

| Caso | Pasos con distancia | Alternancia | En estancias | En traslados |
| --- | ---: | ---: | ---: | ---: |
| Sigmoide, parejas 1.5 m | 63.82% | 86.86% | 61.78% | 71.07% |
| Sigmoide, parejas 0.5 m | 65.50% | 86.72% | 63.48% | 72.55% |
| Potencia 1/4, parejas 1.5 m | 64.41% | 86.32% | 62.27% | 71.90% |
| Uniforme, parejas 1.5 m | 37.57% | 89.37% | 33.54% | 51.69% |

La sigmoide con parejas 1.5 m registra 6512 intentos sin distancia entre
18000 (36.18%). Repite placa el 13.14% de las transiciones. Se comprueba en
cada paso que solo una celda de la slice del reloj es finita y que M conserva
15 slices. Elegir una placa por paso hace que los porcentajes de intentos con
distancia y pasos con distancia sean iguales.

Los resultados históricos siguientes mantenían la placa durante 10 pasos;
no evaluaban el modelo solicitado y no son comparaciones entre pesos válidas
para este modelo corregido.

## Histórico: estancias y traslados con sesiones de 10 pasos

```powershell
python fingerprinting/simular_asignacion.py --layout double --movement cores --pair-spacing-m 1.5
```

La función base del orquestador y del planificador es ahora `sigmoid`, con
pendiente 4 y w_cercania=1 (w_equidad=1). La potencia 1/4 se conserva como comparación.
La separación de cada pareja pasa de 0.5 a 1.5 metros. El radio se ajusta a
4.11 m: sqrt(3.25²+2.5²)=4.1003 m certifica que en cada cuadrante hay al
menos dos placas alcanzables para todo punto del cuadrante.

El reloj permanece entre 60 y 120 segundos en cada núcleo (0.25/0.75 en
ambos ejes), con pequeñas perturbaciones aleatorias y atracción al centro.
Después elige otro núcleo y viaja a aproximadamente 0.2 m/s con pequeño
ruido lateral. Los recorridos son continuos, con pasos inferiores a 0.025
unidades normalizadas; no se teletransporta. Todos los modelos usan los
mismos 20 recorridos de 900 segundos. Las sesiones siguen durando 10 segundos.

La equidad ahora es un bonus condicionado por la cercanía:
`score = cercania * (w_cercania + w_equidad * equidad) / (w_cercania + w_equidad)`.
El crédito de espera se multiplica por `1 - carga`. La carga es una media
exponencial de selecciones por reloj y placa; por decisión se actualiza con
`alpha=2/(W+1)`: sube al repetir y decae al elegir otras placas. No hay veto
ni cupo de alternancia y se mantiene el suelo de aceptación de 0.1.

La alternancia mide el porcentaje de decisiones consecutivas que eligen
placas distintas: cambios / (decisiones - 1). «Repite placa» es su complemento.
Se cuentan todas las asignaciones, tengan o no conexión, y se excluye la
primera decisión porque no tiene anterior. Los segundos de una misma sesión
no se cuentan como repeticiones. Es una métrica; no se prohíbe repetir placa.

La comparación de parejas de 0.5 m conserva el MISMO radio de 4.11 m y el
MISMO movimiento para aislar el cambio de separación.

| Caso | Segundos conectados | En estancias | En traslados | Distancia extra |
| --- | ---: | ---: | ---: | ---: |
| Sigmoide w=1, separación 1.5 m | 48.23% | 44.69% | 60.86% | 2.87 m |
| Sigmoide w=1, separación 0.5 m | 49.06% | 46.04% | 59.71% | 3.09 m |
| Potencia 1/4 w=1, separación 1.5 m | 44.82% | 41.41% | 56.96% | 3.11 m |
| Uniforme, separación 1.5 m | 38.43% | 34.59% | 52.18% | 3.44 m |

La cobertura ideal con sesiones de 10 segundos es del 100% en estos
recorridos, por lo que el aprovechamiento coincide con el tiempo conectado.
Hay como mínimo 3 placas en radio a lo largo de los recorridos concretos;
la garantía geométrica sobre TODO el plano es de 2 placas.
Los porcentajes de fase son medias por recorrido, no pesos con los que
reconstruir el porcentaje total. Cambiar radio y movimiento impide comparar
directamente con los experimentos antiguos de paseo aleatorio.

Con idéntico movimiento, radio 4.11 m y separación 1.5 m, el experimento
anterior con w=5 obtuvo 48.83% de tiempo conectado; bajar a w=2 daba 46.17%
con la equidad anterior. La equidad suave actual eleva ese 46.17% a 49.11%.
Los intentos sin distancia inicial bajan de 953/1800 (52.94%) a 909/1800
(50.50%). La alternancia pasa de 93.93% a 92.70%; las repeticiones son
130/1780 (7.30%). Es una comparación de simulación sobre las mismas semillas,
sin latencia de conexión; no representa todavía una prueba de hardware.

Con la misma equidad suave, bajar w_cercania de 2 a 1 reduce el tiempo con
datos de 49.11% a 48.23%. Los intentos sin distancia inicial suben de
909/1800 (50.50%) a 922/1800 (51.22%). La alternancia sube de 92.70% a
93.88%; las repeticiones bajan de 130/1780 a 109/1780 (6.12%).

## Experimento anterior: doble cobertura

```powershell
python fingerprinting/simular_asignacion.py --layout double
```

Peso restaurado a `w_cercania=5` y `w_equidad=1`. Se mantienen 20 semillas,
900 segundos por recorrido, plano 10 × 10 m y sesiones de 10 segundos.
Los datos reales de sit_placas no se modifican.

Se simulan 9 anchors: dos en cada cuadrante, en x=0.225/0.275 o 0.725/0.775
y en y=0.25 o 0.75; la novena placa queda en (0.5,0.5).
Ambas placas de cada cuadrante están a distancia <=sqrt(2.75²+2.5²)=3.71652 m
de cualquier punto de ese cuadrante. El radio se eleva a 3.72 m, garantizando
dos anchors en todo el cuadrado, incluyendo los límites. Se comprueba también
sobre una malla de 101 × 101 puntos y a lo largo de todos los recorridos.

Con solo 9 placas, el radio 2.5 m no puede garantizar doble cobertura de
100 m²: ni siquiera la suma de áreas de sus discos alcanza 200 m².
El radio 3.72 m es una construcción suficiente, no el mínimo optimizado.

La referencia de posiciones originales usa el MISMO radio 3.72 m para aislar
el efecto de la distribución. Resultados medios:

| Caso | Segundos conectados | Cobertura aprovechada | Distancia extra |
| --- | ---: | ---: | ---: |
| Doble cobertura, potencia 1/4 | 43.67% | 43.68% | 2.68 m |
| Posiciones originales, potencia 1/4 | 36.40% | 38.28% | 3.31 m |
| Doble cobertura, sigmoide | 45.64% | 45.65% | 2.63 m |
| Doble cobertura, uniforme | 31.14% | 31.15% | 3.34 m |

La doble cobertura se verifica el 100% del tiempo; la referencia ideal con
una placa por sesión conecta el 99.98% del tiempo. La estrategia actual
todavía no aprovecha toda esa cobertura. Se mantiene la potencia 1/4 en el
orquestador: la sigmoide es una comparación de simulación.

## Metodología y experimentos anteriores

Desde la raíz de HERA:

```powershell
python fingerprinting/simular_asignacion.py --seconds 900 --seeds 20 --scale-m 10
```

Importa el planificador real y su bloque `adaptive_scheduler` de configuración.
Simula todos los anchors presentes en `fingerprinting/data/sit_placas.csv`.
No cambia la configuración del orquestador ni conecta con MQTT o hardware.

El reloj se mueve cada segundo con inercia aleatoria, limitado a un paso de
0.025 por eje, con reflexión en los límites [0,1]. El plano se supone cuadrado
de 10 × 10 metros porque las coordenadas originales no incluyen escala física.
Cada sesión dura 10 segundos. Solo se entrega al planificador la medida de la
placa elegida; nunca se le revela la posición real ni las distancias restantes.
La conexión solo existe si la distancia real es <=2.5 metros (configurable con
`--connection-radius-m`). Dentro se añade ruido de medida de 8 cm; fuera se
entrega D_max=10 m a la heurística, manteniendo `ok=false` en el resultado.
D_max representa máxima lejanía en la estimación por pareja. El orquestador
también convierte sus intentos sin distancia a D_max dentro de M; los TSV
conservan el marcador -1 para indicar que no fue una distancia física medida.
Se omiten latencias de preparación y transporte.

Compara agregación por pareja de toda M con potencia 1/4 y cercanía ×10,
la misma agregación con cercanía ×5, nueva agregación con sigmoide y cercanía ×10,
selección uniforme y round-robin sobre los mismos recorridos
y los mismos resultados potenciales de radio. La distancia y el ranking se
evalúan al seleccionar una placa; `data_percent` evalúa todos los segundos de
medidas. Los porcentajes de esta simulación no son comparables directamente
con los CSV reales, cuyos timestamps reflejan el guardado al final de sesión.

`resultados.json` conserva parámetros, resultados medios y el recorrido de la
primera semilla para cada política. `replay.html` reproduce ese primer recorrido;
su tabla muestra las medias de las 20 semillas por defecto.

Con `evidence_power=0.25` (alpha=4) se amplifica la magnitud de la evidencia temporal
normalizada mediante potencia, conservando el signo de los fallos. No se aplica
la raíz a las probabilidades finales, pues eso las haría más uniformes.
El modo `selection_mode=rejection` escala las puntuaciones de las placas libres
con min-max al intervalo [`acceptance_floor`, 1], con suelo 0.1. Propone una
placa uniformemente al azar y la acepta si un segundo aleatorio es menor que
su peso; si se rechaza, vuelve a proponer. Con puntuaciones iguales, todos los
pesos son 1. La probabilidad final es el peso dividido por la suma de pesos:
un suelo de aceptación de 0.1 no equivale a un 10% de probabilidad final.
`temperature` y `minimum_probability` solo se aplican al modo `softmax`.

La sigmoide usa `tanh(k*e)/tanh(k)`, con `k=sigmoid_slope=4`, sobre evidencia
firmada e en [-1,1]. Equivale a una sigmoide logística centrada y normalizada.
Se activa con `evidence_transform=sigmoid`; para la potencia, usar `power`.

La puntuación final es `(w_cercania * proximidad + w_equidad * equidad) /
(w_cercania + w_equidad)`. La configuración actual usa 10 y 1; la comparación
anterior conserva también 5 y 1. La lejanía está representada por menor refuerzo
de proximidad, no por un término independiente con peso propio.

`history_aggregation=per_anchor` calcula para cada pareja la distancia media
normalizada con pesos `0.9**edad` sobre todas sus observaciones en M. Un intento
fallido aporta D_max; una celda sin intento no entra en esa media. La confianza
es `0.9**edad_de_la_última_observación`. La evidencia firmada es
`confianza * (1 - 2 * distancia_normalizada)`, propagada mediante la vecindad.
Cada segundo simulado entra una slice; sale la más antigua al superar W=15.
No se conserva ninguna distancia fuera de M. Las estimaciones por reloj se
exponen en `adaptive_scheduler.estimates_by_watch` del estado del orquestador.

Con el radio duro, los porcentajes entre las tres más cercanas son: nueva
agregación con potencia 1/4 y w=10 41.28%, la misma con w=5 40.44%, nueva agregación
con sigmoide y w=10 40.39% y uniforme 34.28%. Los porcentajes de placa elegida dentro
de 2.5 m son 20.61%, 20.50%, 19.72% y 14.11%, respectivamente. Hay algún anchor dentro
de radio en el 80.33% de decisiones.
Con nueva agregación, potencia 1/4 y peso 10 hay un 95.39% de cambios
entre intentos. La simulación verifica límites espaciales, probabilidades
normalizadas y puntuaciones acotadas; no certifica rendimiento de radio real.

## Métricas principales

El top-3 se conserva solo como diagnóstico secundario: depende del número
y distribución de las placas y no mide la magnitud del error.

- `data_percent`: porcentaje de segundos con conexión válida.
- `mean_excess_distance_m`: media por segundo de distancia a la placa elegida
  menos distancia a la placa realmente más cercana. Menor es mejor; cero
  representa la mejor distancia posible en ese instante.
- `oracle_session_data_percent`: cobertura de una referencia ideal que elige
  la placa con más segundos conectables en cada bloque de 10 segundos. Tiene
  la misma restricción de una placa por sesión, pero conoce el futuro solo
  para establecer una cota superior; no es un algoritmo implementable online.
- `coverage_efficiency_percent`: segundos conectados divididos por los de esa
  referencia ideal. No penaliza al modelo por regiones sin cobertura.
- `mean_connected_distance_m`: distancia media durante conexiones exitosas.
  Se interpreta junto a data_percent; aislada puede favorecer una política
  que solo conecta pocas veces y muy cerca. Se devuelve null si no conecta.

Las cifras mostradas son medias de métricas por recorrido (20 semillas), no
cocientes de medias. La referencia ideal logra un 79.35% de segundos conectados;
la versión actual obtiene 20.22% y aprovecha el 25.28% de la cobertura ideal.
La distancia extra media es 3.53 m, frente a 3.94 m de selección uniforme.
Las posiciones, la velocidad, el radio y las duraciones siguen siendo supuestos
del experimento; cambiar la métrica no elimina esas limitaciones.

Subir w de 5 a 10 mantiene 20.22% de segundos conectados y apenas cambia
la distancia extra (3.53365 a 3.53337 m). Min-max elimina la escala global
de las puntuaciones: el peso modifica su relación con la equidad, pero
no convierte por sí solo la distribución en una selección más concentrada.
