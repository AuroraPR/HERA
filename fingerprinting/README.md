# Segmentación para fingerprinting

Ejecutar desde la raíz del repositorio:

```powershell
python fingerprinting/segmentar.py
```

Lee `data/20733.watch_01 (1).tsv` y descubre automáticamente todos los
`data/escena_*.csv` y todos los identificadores de `anchor` del TSV.
Se pueden cambiar las rutas con `--uwb`, `--escenas` y `--salida`.
Si los CSV de escenas tienen otro nombre, usar `--patron-escenas`, por ejemplo
`--patron-escenas "esc_*.csv"` para los ficheros de `data2`.
Volver a ejecutar incorpora las nuevas escenas y anchors.

`segmentacion.csv` contiene una fila por ventana de un segundo, concatenando
las escenas en orden temporal, sin filas para los huecos entre escenas.
`label` conserva el nombre del fichero de escena. Los timestamps originales
se conservan sin convertir de zona horaria: ambos registros deben usar el mismo reloj.
Cada escena abarca desde su primer click hasta el último. Las ventanas empiezan
en el primer click; la última se recorta si dura menos de un segundo.

Las ventanas incluyen el inicio y excluyen el fin, salvo la última, que incluye
el click final. `x,y` son la interpolación lineal entre los clicks que rodean el
centro de cada ventana; se mantienen las unidades de las coordenadas originales.
Nunca se interpola entre escenas.

Por cada anchor se calculan `median`, `min` y `max` de `distance_cm` y `rssi_dbm`.
Las columnas conservan el identificador del anchor. Las estadísticas sin muestras
son `-1`; los valores no finitos, distancias negativas y marcadores `-1` se excluyen
antes de calcularlas. Los RSSI negativos válidos sí se conservan.

Las coordenadas y estadísticas se exportan con cuatro decimales. La columna
final `data` contiene `NO-data` si todas las estadísticas de los anchors son
−1, y `data` si existe alguna medición válida. Los timestamps exportados se
redondean al segundo más cercano y terminan en `.0000`; la asignación de muestras
y la interpolación se calculan con los tiempos originales.
