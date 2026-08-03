# Dudas que me quedaron

Cosas que me crucé haciendo el TP y que no terminé de resolver o de entender
del todo. Las dejo escritas porque me parece más honesto que hacer de cuenta
que no existen.

---

### 1. `openat()` y el reciclado de PIDs

En el README menciono que la forma correcta de leer `/proc/<pid>/` de forma
segura sería abrir un fd al directorio y leer todo relativo a ese fd, para
que el kernel no me deje leer los datos de un proceso distinto si el PID se
recicla en el medio.

Lo que no me queda claro es **si eso realmente lo garantiza**. Si el proceso
muere, ¿el fd al directorio queda inválido y las lecturas fallan (que sería
lo que quiero), o el kernel puede reasignar ese directorio al proceso nuevo?
Leí que `/proc/<pid>` queda "stale" y devuelve `ESRCH`, pero no lo probé.

Tampoco sé cómo se hace desde Python: `os.open()` de un directorio + `os.open()`
con `dir_fd=`, supongo, pero no vi ejemplos claros de esto aplicado a `/proc`.

### 2. Cuál es el CPU% "correcto"

Calculo el CPU% como `(delta_jiffies / CLK_TCK) / delta_tiempo_real * 100`.
Eso da porcentaje **de un core**, así que un proceso con 4 threads full puede
dar 400%.

`htop` por defecto muestra lo mismo, pero tiene una opción para mostrarlo
normalizado por cantidad de cores. No sé cuál de las dos es "la correcta"
conceptualmente, ni cuál se espera en este TP. Dejé la primera porque es la
que coincide con `top`.

Duda relacionada: uso `time.time()` para medir el intervalo real. ¿No sería
más correcto usar los jiffies totales de `/proc/stat` como denominador, así
el cálculo queda enteramente en la escala de tiempo del kernel y no me afecta
si el proceso analizador se atrasa?

### 3. `Priority` en `/proc/<pid>/stat` no coincide con lo que esperaba

El campo 18 (`priority`) me da 20 para procesos normales, pero la man page
dice que para procesos no-RT el valor es `raw_nice + 20`... o algo así, según
la versión del kernel. Para nice 0 me da 20, lo cual cierra, pero no probé
con nice negativo para verificar la fórmula. Tampoco entiendo bien la relación
entre este valor y el que muestra `ps -o pri`, que da un número distinto.

### 4. ¿Está bien que `Manager.dict` no necesite lock?

Entendí que `snapshot[clave] = valor` es atómico porque el Manager lo resuelve
en su proceso servidor, que atiende los pedidos de a uno. Pero no encontré
esto afirmado explícitamente en la documentación de Python, sólo la advertencia
de que las operaciones tipo `d[k].append(x)` **no** son atómicas (que sí
entiendo por qué).

Mi diseño de single-writer hace que la pregunta sea medio académica en mi
caso, pero me quedé con la duda de si técnicamente hacía falta un `Lock`
igual.

### 5. Threads del propio Manager y de las Queues

Cuando abro la vista Threads y me paro sobre los procesos de mi propio
monitor, veo más threads de los que yo creé. Entiendo que las `Queue` de
multiprocessing levantan un "feeder thread" interno para escribir al pipe
sin bloquear, así que algo de eso debe ser.

Lo que no sé es si eso significa que mi arquitectura "multiproceso" es en
realidad medio híbrida sin que yo lo haya decidido, y si eso cuenta en contra
de la consigna (que pide que la arquitectura principal sea multiproceso, cosa
que sí es, pero los threads aparecen igual por debajo).

### 6. Por qué algunos procesos kernel tienen RSS distinto de 0

Asumí que los procesos kernel (los `[kworker/...]`) tienen RSS 0 porque no
tienen espacio de direcciones de usuario. En la mayoría se cumple, pero vi
algunos con valores chicos pero no nulos (6-9 KB). No sé si es un artefacto
de cómo el kernel reporta esos campos, o si estoy leyendo mal algo.

### 7. `SIGWINCH` y curses

Manejo SIGWINCH con `curses.endwin()` + `refresh()`, y funciona, pero lo hice
medio a prueba y error. No tengo claro si es la forma canónica o si debería
usar `curses.resizeterm()` / `curses.resize_term()`. Tampoco sé qué pasa
exactamente si la señal llega justo en medio de un `addstr()`.

### 8. Cuántos procesos son "demasiados"

La consigna pregunta si el monitor escala con 100 terminales abiertas. Con el
recolector central creo que la respuesta es "mejor que antes", pero no lo
medí de verdad. Me gustaría saber cuál sería la forma correcta de perfilar
esto: ¿medir el tiempo de cada ciclo del analizador y loggearlo? ¿usar el
propio monitor para monitorearse a sí mismo?

Intuyo que el cuello de botella real no es el `listdir` sino la
**serialización de las listas grandes al pasarlas por la Queue y al escribirlas
en el Manager**, porque ahí hay pickle de miles de diccionarios. Pero es una
intuición, no lo medí.
