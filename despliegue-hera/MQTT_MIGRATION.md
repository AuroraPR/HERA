# UWB por MQTT: despliegue y validación

## Flujo nuevo

`python orquestador_multi.py` usa `orchestrator.transport="mqtt"` por defecto.
No abre sockets UART/TCP, puertos COM ni procesos de ranging. El dashboard
HTTP sigue disponible; la comunicación con placas y relojes es MQTT.

1. Al descubrir cada dispositivo, el coordinador pide un reset/cierre y espera
   confirmación. Una presencia retained por sí sola no permite asignar una placa.
2. La heurística elige una placa libre y la reserva ANTES de publicar START al reloj.
3. Android prepara el controlador UWB y publica `prepared`, con MAC y session_id.
4. El coordinador envía START a la Pico con esa MAC, el ID UWB y el tiempo restante.
5. La Pico inicializa/configura/arranca UCI localmente, por UART. Se conserva el
   perfil del script Qorvo anterior: DS-TWR, static STS, canal/preamble 9,
   SFD 2, intervalo 120 ms y dirección corta. El sessionId UWB ahora es único
   por intento y se comunica también a Android.
6. La PRIMERA distancia válida se publica en MQTT. La Pico inicia STOP/DEINIT
   localmente, sin esperar una orden del ordenador. Notificaciones UCI fallidas
   no cuentan como distancias válidas: sigue esperando hasta el límite.
7. El coordinador registra una única fila TSV al recibir el resultado, actualiza
   un paso de M y envía STOP idempotente a Pico y reloj.
8. Solo después de `closed` de la Pico y `stopped` de Android se libera la
   placa/reloj y se asigna el siguiente intento. Nunca se comparte una reserva.

`timelimit=5` es el presupuesto desde START al reloj, incluyendo preparación;
no son 5 segundos de preparación más otros 5 de ranging. Android y la Pico
tienen además timeout local. Un fallo/timeout produce -1 en TSV y D_Max en M.
Si UCI no confirma el cierre, la Pico corta y restablece alimentación del DWM
mediante el MOSFET ya conectado a GP2. Esto presupone que ese circuito funciona.
El reinicio necesita 20 ms apagado y 150 ms de arranque, ajustables en main.py
si el hardware lo requiere. No se afirma un plazo de tiempo real estricto para
las confirmaciones de red/Android: sin ACK se mantiene la reserva, incluso si
supera 5 segundos. Liberarla a ciegas rompería la exclusividad.

## Modelo y normalización

Se usa la misma clase AdaptiveAnchorScheduler de las simulaciones validadas:
W=15 pasos, decaimiento 0.9, D_Max=10 m, NaN desconocido excluido de medias,
sigmoide con pendiente 4, w_cercania=w_equidad=1, alternancia suave y aceptación
por rechazo con suelo 0.1. No hay memoria adicional de fallos ni veto a repetir.

M y las muestras internas están normalizadas: distancia_cm / (100 * D_Max_m),
acotada a [0,1]. Un fallo aporta 1 y una celda desconocida NaN/null. El TSV
mantiene centímetros para conservar la medida original del hardware.
Se recupera la propagación exponencial `exp(-distancia_xy / neighbor_scale)`
con `neighbor_scale=0.30` en coordenadas normalizadas. Se aplica el mismo factor espacial a evidencia
positiva y negativa, sin escribir valores en las celdas de placas vecinas.
La sigmoide posterior se conserva. La prueba lineal se descarta por menor data.

Convención de geometría compartida con la simulación: x,y en [0,1], con una
unidad de cada eje equivalente a D_Max metros (actualmente 10 m). La diagonal
puede superar D_Max: la influencia exponencial sigue decreciendo, sin recortar
la distancia geométrica antes de calcularla. No se normalizan los ejes
por separado usando sus máximos ni se ajusta la escala al subconjunto de placas.
IMPORTANTE: sit_placas solo contiene clicks normalizados, sin dimensiones físicas.
Su correspondencia real con esta escala debe calibrarse antes de interpretar
la influencia como metros; no se ha medido ni inferido el tamaño real del plano.
La simulación sí cumple la convención por construcción. Sus distancias y
medidas exportadas son normalizadas; campos explícitos *_m son diagnósticos
en metros y los porcentajes se mantienen como porcentajes.

Cada reloj tiene su contador lógico independiente: terminar una medida avanza
una slice para ese reloj aunque otro reloj tarde más. La vista conjunta M sigue
siendo [edad/paso][reloj][placa]. JSON representa NaN con null. Los TSV conservan
su esquema y ahora usan la hora de recepción, sin esperar al fin de un proceso.
`data/M_latest.json` guarda la matriz más reciente; se inicia una nueva ventana
al reiniciar el programa, no se restauran reservas ni evidencia antiguas.

## Archivos que cargar

En CADA Pico, copiar desde `Server_TCP/Server_TCP/`:

- `main.py` (ya no tiene servidor TCP).
- `anchor_uci.py` (nuevo gestor UCI local).
- `lib/umqtt/simple.py` e `__init__.py`, conservando esa estructura de carpetas.

Configurar `ANCHOR_ID` como la MAC corta física correspondiente y el broker
igual que en `orquestador_config.json`; conservar WiFi y conexiones UART de cada
placa. El main.py del repositorio es la plantilla de 00:01: no cargar ese ID sin
cambiarlo en las demás. La dependencia umqtt incluida tiene I/O acotada para que
una recepción MQTT parcial no bloquee indefinidamente el timeout local.
Los resultados/cierres se reenvían hasta ACK de aplicación y son idempotentes.

En el ordenador, dentro de `UCI_QORVO_UART/UCI_QORVO_UART/`:

```powershell
python -m pip install -r requirements-mqtt.txt
python orquestador_multi.py
```

Mantener `anchor_positions_file` como está. Los campos `serial_port`,
`run_script` y `python_cmd` antiguos no se usan en modo MQTT. La placa que antes
era COM10 también necesita una Pico con el firmware nuevo para participar.
Se ha conservado `cycle.watches=["watch_01"]`: añadir watch_02 únicamente cuando
su aplicación esté configurada con ese ID. Ambos IDs deben ser distintos.

Android: compilar e instalar el módulo **wear** actualizado, no el APK antiguo:

```powershell
.\gradlew.bat :wear:assembleDebug
```

START/STOP/RESET se procesan en una cola serial. STOP comprueba session_id;
un STOP antiguo no cancela la sesión nueva. `stopped` se publica solo después
de cancelar y esperar al job UWB. Se ha quitado la cancelación/espera del mismo
job desde su propio callback de desconexión. Los comandos retained se ignoran;
las presencias usan Last Will offline y los eventos de sesión usan QoS 1.

## Topics

| Topic | Dirección | Contenido |
| --- | --- | --- |
| uwb/commands/WATCH | PC → reloj | start / stop / reset, session_id |
| uwb/session/WATCH | reloj → PC | prepared / stopped, session_id |
| uwb/presence/WATCH | reloj → PC | online / offline |
| uwb/pico/ANCHOR/commands | PC → Pico | start / stop / reset / ack |
| uwb/pico/ANCHOR/events | Pico → PC | result / closed / ready / busy |
| uwb/pico/ANCHOR/status | Pico → PC | salud, batería, protocol=mqtt_uci_v1 |
| uwb/orchestrator/status | PC → observadores | estado, reservas y M |

Ejecutar un solo coordinador para este grupo de dispositivos. Las reservas
son locales al coordinador; no se implementa elección distribuida de líder.

## Verificación realizada

```powershell
python test_adaptive_scheduler.py
python test_mqtt_pipeline.py
```

17 pruebas del algoritmo y 14 del protocolo: primera medida, UART dividido,
peer/handle incorrectos, timeout y reset, duplicados, STOP atrasado, recuperación,
espera de ambos cierres, W independiente por reloj y equivalencia de puntuaciones
con el modelo validado. La prueba completa usa dos relojes y DWM emulado en 200
intentos, con reservas exclusivas y una actualización por intento.
Se verifica también la carga del sit_placas real y la conversión de una medida
MQTT de 687 cm a 0.687 en M, conservando 687 cm en el log. El coordinador falla
al iniciar si alguna placa configurada no tiene posición en ese CSV.

Pendiente de validación física: comportamiento UCI del firmware exacto de los
DWM, GPIO de alimentación, tiempos de arranque y cancelación efectiva del
proveedor UWB de Android. El perfil binario se obtiene del SDK incluido, pero
no sustituye la prueba con placas. La compilación Android no pudo ejecutarse
en este entorno porque no hay Java/JDK en PATH ni JAVA_HOME configurado.
