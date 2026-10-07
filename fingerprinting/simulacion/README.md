# Simulación del planificador

## Experimento actual: doble cobertura

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
