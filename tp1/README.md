# Monitor de Procesos y Threads vía `/proc`

**Trabajo Práctico Nº 1 — Computación II — Universidad de Mendoza — 2026**

Monitor del sistema en tiempo real, estilo `htop`, que extrae toda su
información leyendo `/proc` a mano. Sin `psutil`, sin `subprocess` a `ps`,
sin librerías que abstraigan el kernel: el único "parser" es
[`src/procfs.py`](src/procfs.py).

---

## 1. Descripción general

El monitor muestra dos cosas a la vez:

- Arriba, una **lista de procesos** con estado, CPU%, RSS, threads y comando.
- Abajo, un **panel de detalle** del proceso seleccionado, que cambia según
  cuál de las **7 vistas** esté activa.

Cada vista se alimenta de un **proceso analizador independiente** con su
propio intervalo de refresco, ajustable en vivo. La arquitectura es
multiproceso (no multithread) y usa `Queue`, `Value`, `Manager`, `Event` y
un pipe crudo para la coordinación.

### Uso rápido

```bash
docker compose up --build
```

Y ya estás adentro de la TUI. Para salir, `q`.

### Vistas

| Tecla | Vista | Qué muestra | Default | Mínimo |
|-------|-------|-------------|---------|--------|
| `1` / `r` | Resumen | PID, PPID, UID/GID, usuario, estado, CPU%, RSS, threads, cmdline completo | 2s | 0.5s |
| `2` / `m` | Memoria | VmSize, VmRSS, VmHWM, VmData, VmStk, VmExe, VmLib, VmSwap, page faults, segmentos de `maps` | 3s | 1s |
| `3` / `f` | File descriptors | Lista de FDs con destino y tipo inferido | 5s | 2s |
| `4` / `t` | Threads | LWPs con TID, estado, CPU% y context switches | 2s | 0.5s |
| `5` / `s` | Señales | SigPnd, ShdPnd, SigBlk, SigIgn, SigCgt decodificadas a nombres | 10s | 5s |
| `6` / `p` | Scheduling | nice, priority, policy, RT prio, afinidad, PGID/SID, ctx switches | 10s | 5s |
| `7` / `g` | Sistema | CPU global desglosada, RAM, swap, load, uptime, totales, zombies, Top 3 | 2s | 1s |

### Teclas

| Tecla | Acción |
|-------|--------|
| `1`–`7`, `r/m/f/t/s/p/g` | Cambiar de vista |
| `↑` `↓`, `PgUp` `PgDn` | Navegar la lista |
| `Enter` | Fijar (pin) el proceso seleccionado |
| `/` | Filtrar por nombre de comando |
| `u` | Filtrar por usuario |
| `c` | Rotar orden: CPU% → RSS → PID |
| `+` / `-` | Ajustar el intervalo de la vista activa |
| `h` / `?` | Ayuda |
| `q` | Salir limpiamente |

---

## 2. Diagrama de arquitectura

Son **10 procesos** en total (el padre + 9 hijos), más el proceso interno
que levanta `multiprocessing.Manager()`.

```
                         ┌──────────────────────────────┐
                         │   PROCESO PADRE (main.py)    │
                         │  ┌────────────────────────┐  │
                         │  │  Display / TUI curses  │  │
                         │  │  Handlers de señales   │  │
                         │  │  Watchdog de hijos     │  │
                         │  └────────────────────────┘  │
                         └──┬─────────▲──────────┬──────┘
                            │         │
              Value('d')    │         │ lee
              intervalos    │         │
                            │    ┌────┴─────────────────────┐
                            │    │    SNAPSHOT GLOBAL       │
                            │    │   (Manager.dict)         │
                            │    │  resumen   {datos, ts}   │
                            │    │  memoria   {datos, ts}   │
                            │    │  fds       {datos, ts}   │
                            │    │  threads   {datos, ts}   │
                            │    │  senales   {datos, ts}   │
                            │    │  scheduling{datos, ts}   │
                            │    │  sistema   {datos, ts}   │
                            │    │  tops      {cpu, mem}    │
                            │    └──────────▲───────────────┘
                            │               │ ÚNICO ESCRITOR
                            │        ┌──────┴───────┐
                            │        │  AGREGADOR   │
                            │        └──────▲───────┘
                            │               │ Queue(cola_resultados)
                            │               │
          ┌─────────────────┴───────────────┴──────────────────────┐
          │                                                         │
    ┌─────▼──────┬────────────┬───────┬─────────┬─────────┬────────┬▼────────┐
    │ analizador │ analizador │ anal. │  anal.  │  anal.  │ anal.  │  anal.  │
    │  resumen   │  memoria   │  fds  │ threads │ senales │ sched. │ sistema │
    │    2s      │     3s     │  5s   │   2s    │   10s   │  10s   │   2s    │
    └─────┬──────┴─────┬──────┴───┬───┴────┬────┴────┬────┴───┬────┴────┬────┘
          │            │          │        │         │        │         │
          │  Queue(cola_pedidos): "dame la lista de PIDs"      │         │
          └────────────┴──────────┴────┬───┴─────────┴────────┴─────────┘
                                       │
                            ┌──────────▼───────────┐
                            │     RECOLECTOR       │
                            │  listdir('/proc')    │
                            │  cache con TTL 0.5s  │
                            └──────────┬───────────┘
                                       │
                    Queue(colas_trabajo[nombre]): [pids...]
                                       │
                          (vuelve a cada analizador)


    Señales del SO ──► handler (sólo os.write) ──► SELF-PIPE ──► loop TUI
```

### Flujo de un ciclo

1. El analizador de memoria despierta y pone `"memoria"` en `cola_pedidos`.
2. El recolector lo saca, ve si su cache de PIDs venció (TTL 0.5s), y si
   venció hace `listdir('/proc')`.
3. Le devuelve la lista por `colas_trabajo["memoria"]`.
4. El analizador lee `/proc/<pid>/status` y `/proc/<pid>/maps` de cada PID.
5. Pone `("memoria", datos, timestamp)` en `cola_resultados`.
6. El agregador lo saca y escribe `snapshot["memoria"]`.
7. La TUI lee `snapshot["memoria"]` cuando le toca redibujar.
8. El analizador duerme hasta completar su intervalo, releyendo
   `intervalo.value` por si el usuario lo cambió con `+`/`-`.

---

## 3. Decisiones de diseño

### ¿Por qué un recolector central en vez de que cada analizador lea `/proc`?

La primera versión que escribí tenía a cada analizador haciendo su propio
`listdir('/proc')`. Funcionaba, pero con 7 analizadores eso son **7 escaneos
redundantes del mismo directorio por ciclo**. Con 1000 procesos son 7000
entradas de directorio leídas de más por vuelta, para obtener exactamente
la misma lista.

Centralizar tiene dos ventajas además del costo:

- **Coherencia**: todos los analizadores de un mismo instante trabajan sobre
  el mismo conjunto de PIDs, así que las vistas no se contradicen entre sí.
- **Un solo lugar donde tocar** si más adelante quisiera filtrar qué procesos
  se monitorean.

### ¿Por qué `Queue` y no `Pipe` para repartir el trabajo?

`Pipe` es punto a punto: para 7 analizadores necesitaría 7 pipes y multiplexar
a mano con `select()` del lado del recolector. `Queue` ya resuelve el caso
muchos-a-uno: internamente es un pipe **más un lock más un thread feeder**,
y garantiza que los mensajes de distintos productores no se entremezclen.

Uso `Queue` en las tres direcciones:

- `cola_pedidos` — 7 productores (analizadores) → 1 consumidor (recolector).
- `colas_trabajo[nombre]` — una por analizador, para la respuesta. Acá sí es
  punto a punto, pero uso `Queue` por uniformidad y porque el `maxsize=2` me
  da control de flujo gratis: si un analizador se atrasa, el recolector no
  se le acumula encima.
- `cola_resultados` — 7 productores → 1 consumidor (agregador).

Donde sí uso un **pipe crudo** es en el self-pipe de las señales
([`src/senales.py`](src/senales.py)), porque ahí necesito un file descriptor
sobre el que hacer `select()`, y eso es exactamente lo que un pipe da y una
`Queue` no expone de forma limpia.

### ¿Por qué `Manager.dict` y no `Value`/`Array`?

`Value` y `Array` viven en memoria compartida real (`mmap`) y sólo aceptan
**tipos de tamaño fijo conocido en tiempo de compilación**: un double, un
int, un array de N enteros. Son rapidísimos porque no hay serialización: el
proceso escribe en una página de memoria que el otro ya tiene mapeada.

El snapshot no entra en ese molde. Es un diccionario de listas de
diccionarios, de tamaño variable y desconocido: cuántos procesos hay, cuántos
FDs tiene cada uno, qué largo tiene cada cmdline. Nada de eso se puede
representar en un `Array` sin inventar un formato de serialización propio.

`Manager.dict` sí lo soporta, porque no es memoria compartida: es un
**proceso servidor aparte** al que los demás le hablan por sockets, con los
objetos serializados con `pickle`. Es órdenes de magnitud más lento, pero
maneja estructuras arbitrarias.

Por eso el reparto que hice es:

| Dato | Mecanismo | Por qué |
|------|-----------|---------|
| Snapshot completo | `Manager.dict` | Estructura anidada de tamaño variable |
| Intervalos por vista | `Value('d')` | Un solo double, se lee en cada vuelta del bucle de cada analizador: tiene que ser barato |
| Flag verbose | `Value('i')` | Un entero, lo togglea SIGUSR2 |
| Orden de apagado | `Event` | Es exactamente un flag booleano con espera eficiente |

El caso de los intervalos es el más claro: un `Value` se lee **cada 100ms en
cada uno de los 7 analizadores**. Si eso pasara por el Manager serían 70
round-trips por segundo contra un proceso servidor para leer un número.

### ¿Cómo manejé las race conditions?

**No las sincronicé: las eliminé por diseño.** Hay tres puntos donde podría
haber carrera y cada uno tiene una respuesta distinta:

**a) Varios analizadores escribiendo el snapshot.**
Este es el clásico. Si dos analizadores hicieran `snapshot["x"] = ...` sobre
la misma clave, o peor, un `snapshot["x"].append(...)` (que es read-modify-write
y **no** es atómico en un `Manager.dict`: lee una copia local, la modifica y
la reescribe), se pisarían.

La solución fue el **patrón single-writer**: los analizadores no tocan el
snapshot, mandan sus resultados por `cola_resultados` y **sólo el agregador
escribe**. Con un único escritor, no hay dos escrituras concurrentes que
sincronizar. La `Queue` ya es segura internamente, así que no hay ningún
`Lock` explícito en todo el código.

**b) La TUI leyendo mientras el agregador escribe.**
La TUI sólo lee. Como cada clave se reemplaza entera de un saque
(`snapshot[nombre] = {...}`, que sí es atómico a nivel del servidor del
Manager), la TUI nunca ve una estructura a medio construir: ve la versión
vieja o la nueva. Puede ver una vista un ciclo desactualizada respecto de
otra, y por eso cada clave guarda su timestamp y la barra de título muestra
"datos: hace X.Xs".

**c) `/proc` cambiando mientras lo leo.**
Esta es la race condition que no puedo eliminar, porque el sistema operativo
es el otro "escritor" y no lo controlo. Entre que hago `listdir('/proc')` y
que abro `/proc/<pid>/status`, el proceso puede haber terminado. Peor: entre
leer `stat` y leer `status` del mismo PID, el proceso puede morir y el kernel
puede reciclar ese número para otro proceso nuevo.

Lo mitigo devolviendo `None` ante `FileNotFoundError`/`ProcessLookupError` en
todas las funciones de `procfs.py` (el proceso simplemente no aparece en esa
vuelta), pero **no lo resuelvo**: está documentado en Limitaciones.

### ¿Por qué esos intervalos por defecto?

La lógica fue: cuánto cuesta leerlo × cuán rápido cambia.

- **Resumen (2s)** y **Threads (2s)**: son los datos que el usuario mira para
  ver "qué está pasando ahora". Además el CPU% necesita un delta, y con
  ventanas muy cortas (< 0.5s) el número salta mucho porque el jiffy es
  granular (100 Hz típicamente): en 0.1s hay como mucho 10 jiffies de
  resolución. Por eso el mínimo es 0.5s.
- **Memoria (3s)**: leer `/proc/<pid>/maps` es lo más caro de todo, porque un
  proceso grande puede tener cientos de líneas y hay que sumarlas todas. Y el
  footprint de memoria no cambia tan rápido como el CPU.
- **FDs (5s)**: requiere un `readlink()` por cada FD de cada proceso. Un
  servidor con muchas conexiones puede tener miles. Es la lectura con peor
  escalamiento del monitor.
- **Señales (10s)** y **Scheduling (10s)**: son prácticamente estáticos. El
  `nice`, la política de scheduling y las máscaras de señales de un proceso
  no cambian salvo que alguien las cambie explícitamente. Refrescarlos rápido
  sería gastar CPU para ver el mismo número.
- **Sistema (2s)**: barato de leer (4 archivos globales) y es lo que uno mira
  para el pulso general de la máquina.

---

## 4. Conceptos del curso aplicados

**Clase 3 — Procesos: anatomía y `/proc`.** Todo el TP es esto. El caso más
lindo es el parseo de `/proc/<pid>/stat` en `_separar_comm()`: el campo
`comm` va entre paréntesis y **el kernel no escapa nada**, así que un proceso
llamado `raro) cosa` rompe cualquier parser que haga `split()`. La forma
correcta es buscar el **último** `)` de la línea, porque después del comm el
formato ya no tiene paréntesis. Hay un test específico para este caso
(`test_comm_con_parentesis_y_espacio_juntos`).

**Clase 3 — Memoria virtual.** La vista Memoria muestra la diferencia entre
`VmSize` (espacio de direcciones virtual reservado) y `VmRSS` (páginas
realmente residentes en RAM). Un proceso puede tener 2 GB de VmSize y 30 MB
de RSS: reservó direcciones que nunca tocó, y el kernel no le asignó marcos
físicos. En `leer_segmentos_maps()` agrupo `/proc/<pid>/maps` por permisos,
que es literalmente ver el layout que vimos en clase: `r-x` es el segmento
de texto (código), `rw-` sin ejecución son los datos, más `[heap]` y
`[stack]` explícitos.

**Clase 3 — Page faults.** La vista Memoria distingue minor de major faults.
Un *minor fault* es una página que estaba en RAM pero sin mapear en la tabla
del proceso (barato); un *major fault* obligó a ir al disco (carísimo). Por
eso un proceso con muchos major faults es un proceso que está swapeando o
leyendo mmaps fríos.

**Clase 4 — fork, exec, wait: zombies.** La vista Sistema cuenta los procesos
en estado `Z`. Un zombie es un proceso que ya terminó pero cuyo padre todavía
no llamó a `wait()`, así que el kernel mantiene la entrada en la tabla de
procesos para guardar el exit status. Lo notable es que un zombie tiene RSS 0
(ya liberó su memoria) pero sigue ocupando un PID: por eso el monitor los
resalta con `A_BLINK` en la lista.

**Clase 4 — Jerarquía de procesos.** La vista Resumen muestra el PPID. En la
vista Scheduling muestro además PGID y SID, que son lo que define los grupos
de procesos y las sesiones: cuando apretás Ctrl+C, la terminal manda SIGINT a
**todo el grupo de procesos en foreground**, no a un proceso solo. Eso explica
por qué mis analizadores tienen que ignorar SIGINT explícitamente (ver más
abajo).

**Clase 5 — Pipes y file descriptors.** La vista FDs es directamente esto:
`os.readlink('/proc/<pid>/fd/<n>')` devuelve `pipe:[12345]` para un pipe,
`socket:[...]` para un socket, una ruta para un archivo común. Se puede ver
que dos procesos comparten un pipe porque el número de inodo entre corchetes
coincide. Y en el propio monitor: si te parás sobre el proceso del monitor,
vas a ver los pipes que usan las `Queue` internamente.

**Clase 6 — Señales: máscaras.** `SigBlk`, `SigIgn`, `SigCgt` son bitmaps de
64 bits en hexadecimal donde **el bit N-1 corresponde a la señal N**.
`decodificar_mascara()` recorre los 64 bits y traduce con `signal.Signals(i).name`.
El detalle que me costó entender es el off-by-one: SIGHUP es la señal 1 y está
en el bit 0, así que la máscara `0x2` es SIGINT (señal 2, bit 1), no SIGHUP.
Los bits altos son señales de tiempo real que no tienen nombre simbólico y las
muestro como `SIG34`, `SIG35`, etc.

**Clase 6 — Handlers async-signal-safe y self-pipe.** Esto es lo que más me
cambió la cabeza. Un handler se ejecuta interrumpiendo cualquier cosa, así que
si el handler llama a `print()` o `json.dump()` y justo interrumpió al programa
principal en medio de un `malloc()` o con un lock de la libc tomado, se puede
deadlockear o corromper memoria. Sólo se pueden llamar funciones
async-signal-safe (`write`, `_exit`, `sigaction`...).

Mi handler hace exactamente una cosa:

```python
def _handler(signum, frame):
    os.write(_pipe_escritura, bytes([signum]))
```

El trabajo pesado (releer `config.json`, escribir el dump JSON) lo hace el
loop de la TUI cuando lee ese byte del pipe, ya en contexto normal. Como
bonus, escribir al pipe **despierta el `select()`**, así que la señal se
integra al event loop en vez de necesitar una flag global con polling.

**Clase 7 — Memoria compartida.** Los `Value` son `mmap` de
memoria compartida anónima entre padre e hijos. Es el mecanismo más rápido
justamente porque no hay copia ni serialización: los procesos ven la misma
página física. La contracara es que sólo sirve para tipos de tamaño fijo, y
por eso el snapshot va por `Manager` (ver sección 3).

**Clase 8 — Multiprocessing: `Process` y daemons.** Los 9 hijos se lanzan con
`daemon=True`, lo que hace que Python los mate automáticamente si el padre
termina. Es una red de seguridad contra procesos huérfanos si el padre muere
de forma anormal. Uso `fork` como start method (el default en Linux) porque
los hijos heredan las `Queue` y los `Value` ya construidos sin tener que
re-serializarlos.

**Clase 9 — `Manager` y `Value`.** Ver la tabla de la sección 3, que es toda
esta clase aplicada: qué dato va a memoria compartida real y cuál necesita el
proceso servidor del Manager.

**Clase 10 — Threading, GIL y threads como LWPs.** La vista Threads muestra
que en Linux **un thread es un proceso liviano (LWP) con su propio TID**, y
que aparece en `/proc/<pid>/task/<tid>/`. El thread principal siempre tiene
`TID == PID` (lo marco con `<- main` en pantalla). Esto explica de forma
concreta la diferencia entre PID y TID que en el papel es abstracta.

También conecta con el GIL: si abrís un programa Python multithread CPU-bound
en la vista Threads, vas a ver varios TIDs pero la suma de sus CPU% no pasa
del 100% de un core, porque el GIL serializa la ejecución de bytecode. Un
programa en C o Rust con la misma cantidad de threads sí pasa del 100%.
(En el `/proc` de este contenedor se puede ver con `rclone`, que tiene 22
threads reales.)

**Clase 11 — Sincronización.** La decisión de fondo del TP: en vez de poner
`Lock` sobre el snapshot, lo diseñé con **un único escritor**, que es una
forma de sincronización por arquitectura. El `Event` de salida es la
primitiva de sincronización que sí uso explícitamente, para coordinar el
apagado de los 9 hijos.

---

## 5. Señales

| Señal | Acción |
|-------|--------|
| `SIGINT` (Ctrl+C) | Shutdown limpio: setea el Event, vacía las Queues, joinea los hijos |
| `SIGTERM` | Igual que SIGINT |
| `SIGHUP` | Relee `config.json` y aplica los intervalos, filtros y orden nuevos |
| `SIGUSR1` | Vuelca el snapshot completo a `dumps/dump_<timestamp>.json` |
| `SIGUSR2` | Toggle del modo verbose (más FDs visibles por proceso) |
| `SIGWINCH` | Repinta la pantalla al redimensionar la terminal |

Para probarlas, desde otra terminal:

```bash
# el PID del monitor dentro del contenedor
docker compose exec monitor pgrep -f "python3 main.py" | head -1

kill -HUP  <pid>   # recarga config.json (editalo antes y vas a ver el cambio)
kill -USR1 <pid>   # aparece un JSON en ./dumps/
kill -USR2 <pid>   # la barra de estado muestra "verbose: ON"
kill -TERM <pid>   # cierre limpio
```

### Detalle: por qué los hijos ignoran SIGINT

Cuando apretás Ctrl+C, la terminal manda SIGINT a **todo el grupo de procesos
en foreground**, no sólo al padre. Como los hijos heredan los handlers del
padre en el `fork()`, cada uno de los 9 ejecutaría el handler del padre y
escribiría en el self-pipe, lo cual no tiene sentido.

Por eso cada hijo hace `signal.signal(SIGINT, SIG_IGN)` al arrancar y confía
en el `Event` compartido para saber cuándo terminar. El apagado tiene un
único responsable: el padre.

---

## 6. Limitaciones conocidas

1. **`/proc` no es atómico y los PIDs se reciclan.** Entre leer `stat` y
   `status` del mismo PID, el proceso puede morir y el kernel puede reasignar
   ese número. En ese caso el monitor mostraría datos mezclados de dos
   procesos distintos. La forma correcta de blindarlo sería abrir un fd al
   directorio `/proc/<pid>/` y leer todo relativo a ese fd con `openat()`,
   porque el directorio queda "clavado" al proceso original. No lo implementé.

2. **El CPU% del primer ciclo siempre es 0.** Es inevitable: sin lectura
   anterior no hay delta. Los procesos recién nacidos aparecen con 0% hasta
   el segundo refresco.

3. **El CPU% puede pasar de 100%.** Es correcto y no es un bug: un proceso con
   4 threads en 4 cores usa 400%. No lo normalizo por cantidad de cores porque
   así es como lo reporta `top` también, pero puede confundir.

4. **Los FDs de procesos de otros usuarios no se ven.** `/proc/<pid>/fd/`
   requiere ser dueño del proceso o root. Dentro del contenedor corremos como
   root y con `pid: host`, así que se ven casi todos; fuera de Docker, corriendo
   como usuario normal, la vista FDs queda casi vacía para los procesos de
   sistema. Devuelvo `None` y el panel avisa.

5. **La vista FDs no escala bien.** Hace un `readlink()` por FD por proceso.
   En una máquina con un servidor de miles de conexiones, ese analizador se
   va a atrasar respecto de su intervalo. La mitigación actual es el tope de
   FDs por proceso (12 normal / 128 en verbose) y el intervalo alto por
   defecto, pero el `listdir` completo se hace igual.

6. **La lista de procesos se ordena en el display, no en el analizador.** Con
   miles de procesos, ordenar en cada frame (cada 250ms) es trabajo repetido.
   Lo dejé así porque el orden lo elige el usuario en vivo y moverlo al
   analizador implicaría comunicar el criterio de orden hacia abajo.

7. **El filtro se aplica sólo a la lista de la vista Resumen**, no a lo que
   recolectan los analizadores. Es decir, filtrar no reduce el trabajo de
   lectura de `/proc`, sólo lo que se muestra.

8. **Si el `Manager` muere, el monitor no se recupera.** El watchdog revive
   analizadores, el recolector y el agregador, pero no el proceso servidor del
   Manager. Si eso pasa, todas las lecturas del snapshot fallan.

9. **Zombies y procesos kernel**: los procesos kernel no tienen `cmdline`, así
   que muestro su `comm` entre corchetes (igual que `ps`). Los zombies tampoco
   tienen `maps` ni FDs legibles, así que sus paneles de detalle salen vacíos.

10. **No hay scroll en el panel de detalle.** Si un proceso tiene 128 FDs en
    modo verbose y la terminal es chica, sólo se ven los que entran.

---

## 7. Cómo correr y testear

### Con Docker (forma recomendada)

```bash
git clone <url-del-repo>
cd <repo>
docker compose up --build
```

`docker-compose.yml` usa `pid: "host"`, que hace que el contenedor comparta el
namespace de PIDs del host. Sin eso, `/proc` sólo mostraría los procesos de
adentro del contenedor (unos pocos) y el monitor sería bastante aburrido.

También monta `./config.json` y `./dumps/` como volúmenes, así podés editar la
config desde el host y recargarla con SIGHUP sin reconstruir la imagen, y los
dumps de SIGUSR1 te quedan accesibles.

### Sin Docker

```bash
cd src
python3 main.py
```

Requiere Python 3.11+ y Linux. No hay dependencias externas: `curses` es
stdlib.

### Cómo verificar que los datos son correctos

La forma más honesta de testear el monitor es comparar contra las herramientas
reales. Dentro del contenedor está `procps` instalado justamente para eso:

```bash
docker compose exec monitor bash

ps -eLf | wc -l                    # vs "Threads" de la vista Sistema
ps -o pid,ppid,state,nice,pri -e   # vs las vistas Resumen y Scheduling
cat /proc/loadavg                  # vs el load average de la vista Sistema
ls -l /proc/1/fd/                  # vs la vista FDs para el PID 1
chrt -p 1                          # vs la política de la vista Scheduling
```

Para ver el monitor reaccionar, generá carga:

```bash
# CPU-bound: subí la vista Sistema y mirá el CPU% y los ctx switches involuntarios
yes > /dev/null &

# Un zombie: la vista Sistema debería contarlo
python3 -c "import os,time; os.fork() or time.sleep(60)" &
```

---

## 8. Decisiones sobre la TUI

**`curses` y no `rich`.** `rich` da colores y tablas más lindas con menos
código, pero está pensado para *renderizar salida*, no para una aplicación
interactiva con teclado: para eso hay que combinarlo con `prompt_toolkit` o
un loop propio. `curses` es la herramienta nativa para esto, maneja el
teclado sin bloquear (`timeout()`), y sobre todo es **una interfaz directa a
terminfo**, que es más coherente con el espíritu del TP de tocar las cosas a
bajo nivel. Además evita agregar dependencias: `requirements.txt` quedó vacío.

**Layout de lista arriba y detalle abajo.** La consigna lo pide así, pero
además tiene sentido: la lista da el contexto ("¿qué proceso miro?") y el
detalle da la profundidad. La lista ocupa un tercio y el detalle dos tercios,
porque los paneles de FDs y Threads necesitan lugar.

**Un problema real que encontré con las flechas.** Al principio tenía la tecla
ESC (código 27) como sinónimo de "salir". Resulta que las flechas mandan
secuencias que **empiezan con ESC** (`ESC [ B` para abajo), así que si curses
llegaba a entregar ese byte suelto, el monitor se cerraba al apretar una
flecha. Lo saqué, y además agregué en `_leer_tecla()` un fallback que decodifica
a mano las secuencias `ESC [ x` y `ESC O x`, porque según el terminfo y si estás
en tmux o no, la terminal manda una u otra forma.

---

## 9. Lo que aprendí

Lo que más me sorprendió fue **cuánto de un proceso es simplemente texto en
un directorio**. Uno estudia que el kernel mantiene una estructura de datos
por proceso, pero verlo como archivos que podés `cat` es otra cosa. Y entender
que `/proc` no es un directorio de verdad —no ocupa disco, se genera al
momento en que lo leés— explica por qué las lecturas no son atómicas: cada
`open()` dispara código del kernel que arma la respuesta ahí mismo.

El concepto que más me costó, y el que creo que más me sirvió, fue el de
**async-signal-safety**. Yo venía asumiendo que un handler de señal era
"código normal que corre cuando llega la señal". Entender que interrumpe al
programa en un punto arbitrario —posiblemente en medio de un `malloc()` con
un lock tomado— y que por eso ni siquiera podés hacer un `print()`, me cambió
la forma de pensar la concurrencia. El self-pipe es una solución elegantísima:
convierte un evento asincrónico e incómodo en un file descriptor legible, que
es algo que el event loop ya sabe manejar. Pasar de "flag global + polling" a
"un byte en un pipe que despierta el `select()`" fue el momento en que la
clase 6 me hizo clic.

La tercera cosa fue darme cuenta de que **la mejor sincronización es la que no
hace falta escribir**. Mi primer diseño tenía a los 7 analizadores escribiendo
el `Manager.dict`, y me puse a pensar dónde poner locks para que no se
pisaran. Cuando reordené la arquitectura para que hubiera un único escritor
(el agregador), el problema simplemente dejó de existir: no hay ningún `Lock`
en todo el código, y no porque haya ignorado el problema, sino porque el flujo
de datos está diseñado para que no aparezca. Ahora entiendo mucho mejor por
qué el modelo de actores y los canales de Go existen: es esta misma idea,
sistematizada.

---

## 10. Estructura del repo

```
.
├── README.md                    ← este informe
├── dudas.md                     ← lo que me quedó sin resolver
├── Dockerfile
├── docker-compose.yml
├── requirements.txt             ← vacío a propósito: sólo stdlib
├── config.json                  ← intervalos, mínimos, filtros, verbose
├── src/
│   ├── main.py                  ← entry point, IPC, watchdog, apagado
│   ├── recolector.py            ← lista /proc y reparte PIDs
│   ├── agregador.py             ← único escritor del snapshot
│   ├── display.py               ← TUI curses
│   ├── procfs.py                ← todo el parseo de /proc
│   ├── senales.py               ← self-pipe, config, dump
│   └── analizadores/
│       ├── resumen.py
│       ├── memoria.py
│       ├── fds.py
│       ├── threads.py
│       ├── senales.py
│       ├── scheduling.py
│       └── sistema.py
└── dumps/                       ← acá caen los dumps de SIGUSR1
```

---

## 11. Extensiones opcionales

No implementé ninguna de las extensiones opcionales de la consigna. Preferí
concentrarme en que lo obligatorio quedara sólido y en poder explicar cada
decisión del código.

Una cosa que sí agregué, aunque no está en la lista de extensiones, es un
**watchdog de procesos hijos**: el padre chequea cada 2 segundos que los 9
hijos sigan vivos y relanza al que se haya caído, avisando en pantalla. No lo
cuento como bonus; lo hice porque un monitor que se queda mudo cuando un
analizador muere no sirve de mucho. Se puede probar con:

```bash
# desde otra terminal, matar un analizador a lo bruto
pkill -9 -f "analizador-memoria"
```

En menos de 2 segundos la vista Memoria vuelve a actualizarse y la barra
inferior muestra el aviso del watchdog.
