# Plataforma de Tareas Distribuidas

**Trabajo Práctico Nº 2 — Computación II — Universidad de Mendoza — 2026**

Un servicio HTTP al que se le encargan trabajos, que los ejecuta de forma
concurrente con un pool de workers (corrutinas sobre una `asyncio.Queue`) y
permite consultar su estado, cancelarlos y ver estadísticas. Todo en un solo
hilo de event loop, salvo el trabajo CPU-bound, que se manda a un pool de
procesos.

---

## 1. Descripción y uso

### Levantar

```bash
docker compose up --build
```

La API queda en <http://localhost:8000> y la documentación interactiva
(generada por FastAPI) en <http://localhost:8000/docs>.

Sin Docker (Python 3.11+):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

### Endpoints

| Método | Ruta | Respuesta |
|--------|------|-----------|
| `POST` | `/tareas` | `202` con la tarea (`id`, `estado: pendiente`). `422` si el cuerpo es inválido. `503` si la cola está llena o el servidor se apaga |
| `GET` | `/tareas?estado=&limite=&offset=` | Lista (default 100, máx 1000), en orden de creación |
| `GET` | `/tareas/{id}` | Estado y resultado. `404` si no existe |
| `DELETE` | `/tareas/{id}` | Cancela una tarea `pendiente` o `ejecutando`. `409` si ya terminó |
| `GET` | `/estadisticas` | Totales por estado, tiempos promedio, workers ocupados, cola |

Estados: `pendiente` → `ejecutando` → `completada` / `fallida` / `cancelada`
(una `pendiente` también puede pasar directo a `cancelada`).

### Ejemplos con `curl`

**`esperar`** — duerme N segundos (sirve para ver estados y probar cancelación):

```bash
curl -s -X POST localhost:8000/tareas -H 'Content-Type: application/json' \
  -d '{"tipo": "esperar", "parametros": {"segundos": 10}}'
```

**`hashear`** — SHA-256 iterado (CPU-bound):

```bash
curl -s -X POST localhost:8000/tareas -H 'Content-Type: application/json' \
  -d '{"tipo": "hashear", "parametros": {"texto": "hola", "iteraciones": 5000000}}'
```

**`descargar`** — lista de URLs descargadas concurrentemente:

```bash
curl -s -X POST localhost:8000/tareas -H 'Content-Type: application/json' \
  -d '{"tipo": "descargar", "parametros": {"urls": ["https://example.com", "https://www.python.org"]}}'
```

**Timeout propio** (default: 60 s). Al vencer, la tarea queda `fallida` con el motivo:

```bash
curl -s -X POST localhost:8000/tareas -H 'Content-Type: application/json' \
  -d '{"tipo": "esperar", "parametros": {"segundos": 30}, "timeout": 2}'
```

**Consultar, filtrar, cancelar, estadísticas:**

```bash
curl -s localhost:8000/tareas/<id>
curl -s "localhost:8000/tareas?estado=ejecutando"
curl -s -X DELETE localhost:8000/tareas/<id>
curl -s localhost:8000/estadisticas
```

Resultado típico de `descargar` (una URL caída no tira abajo las demás):

```json
{
  "descargas": [
    {"url": "https://example.com/", "codigo": 200, "bytes": 577, "segundos": 0.366},
    {"url": "http://localhost:1/nada", "codigo": null, "bytes": 0, "segundos": 0.12,
     "error": "ClientConnectorError: Cannot connect to host localhost:1 ..."}
  ],
  "exitosas": 1,
  "segundos_totales": 0.706
}
```

### Apagado ordenado

`docker compose stop` (o `Ctrl+C` / `kill -TERM`) deja de aceptar tareas
nuevas, espera hasta `TIEMPO_APAGADO` segundos a las que están en curso,
cancela las que no terminaron y escribe `datos/estado_<timestamp>.json`. Al
arrancar, si hay un volcado previo, se reporta por log:

```
INFO app.persistencia: Volcado previo encontrado: estado_20261030_194633.json
  (generado 2026-10-30T19:46:33, 5 tareas, por estado: {...}). No se restaura: ...
```

### Configuración (variables de entorno)

| Variable | Default | Qué controla |
|----------|---------|--------------|
| `WORKERS` | `10` | Cantidad de workers (corrutinas) del pool |
| `MAX_COLA` | `5000` | Tareas pendientes máximas; más allá, `POST` devuelve `503` |
| `TIMEOUT_DEFECTO` | `60` | Timeout por tarea (s) si el cliente no manda uno |
| `HASH_MODO` | `proceso` | `proceso` \| `hilo` \| `bloqueante` (este último, solo para medir) |
| `CPU_WORKERS` | nº de núcleos | Tamaño del pool de procesos/hilos para `hashear` |
| `HASH_CHUNK` | `200000` | Iteraciones de hash por viaje al executor |
| `MAX_CONEXIONES` | `100` | Conexiones simultáneas del cliente HTTP |
| `MAX_BYTES_DESCARGA` | 50 MB | Tope por URL |
| `TIEMPO_APAGADO` | `20` | Segundos de gracia a las tareas en curso al apagar |
| `DIR_ESTADO` | `.` (`/datos` en Docker) | Dónde se escribe `estado_*.json` |

---

## 2. Arquitectura

```
  Cliente HTTP
       │
       ▼
┌────────────────────────────────────────────────────────────────────┐
│  Un solo proceso, un solo hilo de event loop (uvicorn)             │
│                                                                    │
│  ┌──────────────┐  encolar()  ┌──────────────────┐                 │
│  │ API FastAPI  │────────────>│  asyncio.Queue   │  (ids, acotada) │
│  │ POST/GET/DEL │             └────────┬─────────┘                 │
│  └──────┬───────┘                      │ await get()               │
│         │ lee/escribe                  ▼                           │
│         │              ┌───────────────────────────┐               │
│         │              │ Pool: N workers (corrut.) │               │
│         ▼              └─────┬─────────┬─────────┬─┘               │
│  ┌──────────────┐            │ crea una asyncio.Task por tarea     │
│  │ dict tareas  │<───────────┘ (done-callback fija el estado final)│
│  │ + contadores │                      │                          │
│  └──────────────┘        ┌─────────────┼─────────────┐            │
│                          ▼             ▼             ▼            │
│                     descargar       esperar       hashear         │
│                     (aiohttp,     (asyncio.sleep)    │            │
│                     TaskGroup)                       │ run_in_    │
└──────────────────────────────────────────────────────┼─executor──┘
                                                       ▼
                                         ┌───────────────────────────┐
                                         │ ProcessPoolExecutor       │
                                         │ (spawn, 1 proc por núcleo)│
                                         └───────────────────────────┘
```

### Flujo de una tarea

1. `POST /tareas` valida el cuerpo (pydantic), crea la `Tarea` en estado
   `pendiente`, hace `put_nowait` del id en la cola y **responde `202` ya**. Si
   la cola está llena responde `503`.
2. Un worker libre hace `await cola.get()`. Si la tarea fue cancelada mientras
   esperaba, la descarta. Si no, crea una `asyncio.Task` interna para
   ejecutarla, la pasa a `ejecutando` y espera a que termine.
3. La tarea interna corre el handler dentro de `asyncio.timeout(...)`.
4. Cuando la tarea interna termina (bien, con error, por timeout o cancelada),
   un **done-callback síncrono** (`Gestor._finalizar`) fija el estado final,
   guarda el resultado o el motivo y actualiza los contadores.
5. El cliente consulta `GET /tareas/{id}`.

### Estructura del código

| Archivo | Rol |
|---------|-----|
| [app/api.py](app/api.py) | Endpoints, `lifespan`, manejo de señales |
| [app/gestor.py](app/gestor.py) | Cola, pool de workers, cancelación, timeouts, apagado, estadísticas |
| [app/tareas.py](app/tareas.py) | Las tres tareas (`descargar`, `hashear`, `esperar`) |
| [app/cpu.py](app/cpu.py) | El hash iterado; módulo mínimo que importan los procesos hijos |
| [app/modelos.py](app/modelos.py) | Estados, entidad `Tarea`, esquemas pydantic |
| [app/persistencia.py](app/persistencia.py) | Volcado a JSON (atómico) y reporte del volcado previo |
| [app/config.py](app/config.py) | Configuración por variables de entorno |
| [bench/](bench/) | Scripts de las tres mediciones |

---

## 3. Decisiones de diseño

### ¿Por qué `asyncio.Queue` y no `queue.Queue`?

`queue.Queue.get()` **bloquea el hilo** hasta que haya un elemento. Si un
worker corrutina la llamara, congelaría el único hilo del event loop: ni los
otros workers ni la API avanzarían. `asyncio.Queue.get()` es una corrutina:
cuando no hay nada, el worker se **suspende** (`await`) y el loop sigue
atendiendo requests. Además `queue.Queue` está pensada para sincronizar
hilos con locks internos, que acá no hacen falta. Y como bonus, `maxsize` da
contrapresión gratis: `put_nowait` lanza `QueueFull` y el `POST` responde
`503` en vez de acumular memoria sin límite.

### ¿Por qué no hace falta un `Lock`?

Todo el estado mutable (`tareas`, los contadores, la cola) se toca solo desde
corrutinas del mismo hilo, y el event loop **solo puede cambiar de corrutina
en un `await`**. Las transiciones de estado (`_cambiar`, `_finalizar`,
`encolar`) son código síncrono sin `await` en el medio: nadie puede verlas a
medias. Un `Lock` serviría con threads, donde el sistema operativo puede
interrumpir en cualquier instrucción.

Esto también explica una elección: el estado final lo fija un **done-callback
síncrono** y no código después de un `await`, para que no haya una ventana
entre "la tarea terminó" y "el estado lo refleja".

### ¿Cómo evito que una tarea CPU-bound congele la API?

Un `for` de 10 millones de iteraciones de SHA-256 no tiene ningún `await`:
mientras corre, el loop no puede atender nada, porque el cambio de corrutina
es cooperativo. `asyncio` resuelve el I/O-bound (esperar sin ocupar CPU),
**no** el CPU-bound (hay CPU que gastar sí o sí). La salida es mover ese
trabajo **fuera del hilo del loop** con `loop.run_in_executor`, y el `await`
sobre el future devuelve el control al loop mientras otro lado calcula.

Elegí un **`ProcessPoolExecutor`** y no un `ThreadPoolExecutor` por el GIL:
en un hilo, el hash sigue compitiendo por el GIL con el hilo del loop. La
medición 2 lo muestra con números: con hilos la API sigue respondiendo pero
~7 veces más lenta que sin carga, y el cálculo tarda más; con procesos la
latencia es igual a la de un servidor ocioso.

Dos detalles que importan:

- **El trabajo se parte en trozos** de `HASH_CHUNK` iteraciones y cada trozo es
  un `run_in_executor` aparte ([app/tareas.py](app/tareas.py), `hashear`). Entre
  trozos hay un `await`, o sea un punto donde la tarea puede cancelarse o vencer
  su timeout. Con un único `run_in_executor` enorme, `DELETE` y el timeout no
  podrían interrumpir nada: el proceso seguiría quemando CPU hasta terminar.
- **Contexto `spawn`**: los procesos se crean con `spawn` en todos los sistemas
  (comportamiento idéntico en Linux y macOS, y no se hace `fork` de un proceso
  que ya tiene un event loop). Por eso [app/cpu.py](app/cpu.py) es mínimo (cada
  hijo lo re-importa) y [app/main.py](app/main.py) tiene la guarda
  `if __name__ == "__main__"`.

### ¿Cómo implementé la cancelación y qué pasa con los recursos a medio usar?

Cada tarea en ejecución es una `asyncio.Task` propia, guardada en
`Tarea.asyncio_task`. Hay dos casos ([app/gestor.py](app/gestor.py), `cancelar`):

- **`pendiente`**: no se la saca de la cola (`asyncio.Queue` no permite borrar
  del medio). Se la marca `cancelada` y el worker que la desencole la descarta
  (*lazy deletion*). Cuesta un slot de la cola hasta que se desencole.
- **`ejecutando`**: `asyncio_task.cancel()` inyecta `CancelledError` en el
  `await` donde la tarea está suspendida. `DELETE` espera a que el callback
  registre el estado, así la respuesta ya dice `cancelada` de verdad.

**Los recursos se liberan porque cada tarea los abre con `async with`**, y la
`CancelledError` desenrolla esos bloques como cualquier excepción:

- `descargar`: `async with session.get(...)` cierra la conexión (comprobado:
  **20 conexiones en uso → 0** tras cancelar a mitad). Las descargas hijas
  viven en un `asyncio.TaskGroup`: al cancelarse la tarea madre, el grupo
  cancela a las hijas **y espera a que terminen** antes de salir, así que no
  quedan corrutinas huérfanas.
- `esperar`: el `sleep` simplemente se interrumpe.
- `hashear`: el `await` sobre el future del executor se cancela al instante.

**Lo que no se puede evitar** (y lo digo en lugar de esconderlo): un trozo que
*ya está ejecutándose* en un proceso del pool no se puede interrumpir; termina
y su resultado se descarta. Es el motivo de partir en trozos: el desperdicio
tiene un tope (~0,1 s de CPU por defecto). `DELETE` sobre un `hashear` de 2.000
millones de iteraciones respondió en 52 ms.

Un `asyncio.timeout()` envuelve a cada tarea; si vence, cancela por dentro y
se convierte en `TiempoAgotado` (la tarea queda `fallida` con
`"timeout: superó los Ns"`). Se distingue del `TimeoutError` que pueda lanzar
una librería con `cm.expired()`.

### ¿Qué pasa si un worker lanza una excepción no capturada?

Hay tres capas:

1. **Excepciones de la tarea del usuario** (error de red, bug del handler,
   etc.): no llegan al worker. Viven dentro de la `asyncio.Task` interna y el
   done-callback las convierte en `fallida` + mensaje + traceback en el log.
2. **Excepciones del propio worker** (un bug en el bookkeeping): un
   `except Exception` en el bucle las loguea y el worker **sigue vivo**. Si la
   tarea acababa de ser desencolada y seguía `pendiente`, se la marca `fallida`
   (`_abortar`); sin esto quedaría `pendiente` para siempre (lo encontré
   probando).
3. **Muerte real del worker** (una `BaseException`): un done-callback lo
   **supervisa** y lanza un reemplazo. Probado inyectando una excepción: el
   pool vuelve a tener su tamaño y las demás tareas siguen.

### ¿Por qué `aiohttp` y no `httpx`?

Las dos sirven. Elegí `aiohttp` porque está construida desde cero sobre
`asyncio` (streams y sockets no bloqueantes), que es exactamente lo visto en
las clases de asyncio en red; su `ClientSession` administra el pool de
conexiones y se comparte entre todas las tareas (se crea en el arranque y se
cierra en el apagado). `httpx` tiene una API más cómoda y soporta modo sync,
pero acá esa ventaja no pesa y no necesito HTTP/2. El cuerpo se lee en
trozos de 64 KB contando bytes, sin guardarlo en memoria.

### Apagado ordenado

[app/api.py](app/api.py) (`_encadenar_senales`) envuelve el handler de
SIGTERM/SIGINT que uvicorn ya instaló: apenas llega la señal baja el flag
`aceptando` (los `POST` pasan a `503`) y llama al handler original. Después,
el `lifespan` ejecuta `Gestor.apagar()`: espera hasta `TIEMPO_APAGADO` a las
tareas en curso, cancela las que no terminaron (`cancelada`, motivo
"interrumpida por apagado"), cancela los workers ociosos, cierra el pool y la
sesión, y vuelca el JSON con `asyncio.to_thread` (escritura a temporal +
`os.replace`, atómica). Las tareas que seguían en la cola quedan `pendiente`
en el volcado: nunca se ejecutaron y no se inventa un estado.
Se usa `signal.signal` (no `loop.add_signal_handler`) porque este último
pisaría el handler de uvicorn y no existe en Windows.

Probado: con `TIEMPO_APAGADO=4`, una tarea de 2 s terminó, una de 60 s fue
interrumpida a los 4 s, la pendiente quedó `pendiente`, y el proceso terminó
con el JSON escrito.


## 4. Mediciones

Todas corridas por los scripts de [bench/](bench/) contra la aplicación real
(cada experimento levanta su propio servidor con la configuración que
necesita). Datos crudos en [resultados/](resultados/).

> **Entorno:** Python 3.12.3, WSL2 sobre Linux 6.18, 12 núcleos lógicos, 7,6 GB
> de RAM. Cliente y servidor corren en la misma máquina, así que se compiten
> los núcleos. La medición 1 son 3 repeticiones (mediana); las 2 y 3 son una
> corrida: ver la sección de limitaciones sobre el ruido.

Para reproducirlas:

```bash
docker compose run --rm -v ./resultados:/srv/resultados api python -m bench.medicion_1_io
docker compose run --rm -v ./resultados:/srv/resultados api python -m bench.medicion_2_loop
docker compose run --rm -v ./resultados:/srv/resultados api python -m bench.medicion_3_techo
```

### Medición 1 — Concurrencia I/O: 20 descargas

Las URLs apuntan a un servidor local de prueba con 500 ms de latencia fija
(sin depender de internet), 20 KB por respuesta.

| Escenario | Tiempos (s) | Mediana | Speedup |
|-----------|-------------|---------|---------|
| **Librería, `for` + `await`** (secuencial) | 10,054 · 10,054 · 10,052 | **10,05 s** | 1× |
| **Librería, `gather`** (concurrente) | 0,516 · 0,512 · 0,513 | **0,51 s** | **19,6×** |
| **API A**: `WORKERS=1`, 20 tareas de 1 URL | 10,088 · 10,078 · 10,093 | 10,09 s | 1× |
| **API B**: 1 tarea con 20 URLs | 0,556 · 0,547 · 0,563 | 0,56 s | 18,1× |
| **API C**: `WORKERS=20`, 20 tareas de 1 URL | 0,573 · 0,573 · 0,588 | 0,57 s | 17,6× |

**Interpretación.** La suma de 20 × 0,5 s es 10 s: es lo que da el secuencial
(10,05). Concurrente, el tiempo es el de la más lenta (0,5 s) más un poco de
overhead: 19,6× sobre 20 posibles. Por la API el speedup es algo menor (18×)
por el costo de crear tareas, encolarlas y el polling del script de medición.
Lo importante: **sin ningún hilo extra**, un solo hilo tiene 20 sockets
esperando a la vez porque mientras uno espera la red, el loop atiende a los
otros. B y C dan lo mismo: da igual que la concurrencia esté *dentro* de una
tarea (`TaskGroup`) o *entre* tareas (workers).

*Extra, internet real* (20 URLs a 10 sitios, 2 pedidos a cada uno): `for` +
`await` 5,06 s de mediana (rango 3,7–8,1 s, muy ruidoso) contra `gather` 0,57 s
→ ~8,9×. Es más bajo que el ideal porque el secuencial reutiliza conexiones
TLS y porque el total concurrente lo fija el sitio más lento. Depende de mi
red de ese momento; el número reproducible es el de arriba.

### Medición 2 — El event loop bloqueado

Una tarea `hashear` de ~6 s de CPU (11,5 millones de iteraciones; esta máquina
hace ~1,9 M/s por núcleo). Mientras corre, un cliente repite `GET /tareas`
cada 20 ms y mide cuánto tarda cada respuesta.

| `HASH_MODO` | Cómo se ejecuta | Respuestas durante la tarea | `GET /tareas` p50 | p99 | **máx** | Tarea completa |
|-------------|-----------------|------|--------|-----|---------|------|
| (servidor ocioso) | — | 66 en 1,5 s | 2,5 ms | 3,6 ms | 3,6 ms | — |
| `bloqueante` | en el event loop, **sin** executor | **1** | 6.246 ms | 6.246 ms | **6.246 ms** | 6,25 s |
| `hilo` | `run_in_executor` + `ThreadPoolExecutor` | 192 | 17,9 ms | 34,9 ms | 49,5 ms | 7,46 s |
| `proceso` | `run_in_executor` + `ProcessPoolExecutor` | 287 | **1,6 ms** | 3,3 ms | 3,6 ms | 6,41 s |

El `POST /tareas` respondió en 4–5 ms en los tres modos (el worker recién
empieza a calcular después de responder).

**Interpretación.**

- **Sin executor, el servidor deja de existir durante 6,2 s.** En todo ese
  lapso se completó *una sola* respuesta, y fue la que estaba esperando desde
  el principio. No hay concurrencia posible: el hash no tiene ningún `await`,
  así que el loop no recupera el control. El `GET` no estaba "lento": estaba
  **esperando su turno**. Es lo que `asyncio` no puede arreglar.
- **Con proceso, la API es indistinguible de un servidor ocioso** (p50 1,6 ms
  contra 2,5 ms; la diferencia es ruido). El cálculo ocupa otro núcleo, en otro
  intérprete y con otro GIL.
- **Con hilo, la API vive pero degradada**: ~7× más lenta (17,9 ms contra 2,5 ms) y la tarea
  tarda 16 % más (7,46 s contra 6,4 s). Es el GIL: el hilo calculador y el hilo
  del loop se turnan el intérprete (cada 5 ms por defecto), así que ambos
  pierden. Un hilo sirve para I/O bloqueante de librerías sin soporte async; no
  para CPU.

### Medición 3 — Techo del pool

El "techo" depende de qué recurso se agote, así que medí cinco.

#### a) Techo del pool de workers (`esperar` 1 s, `WORKERS=50`)

| Tareas | Total | Ideal `ceil(K/50)` | Sobrecosto |
|-------:|------:|-------:|-----:|
| 50 | 1,13 s | 1 s | +0,13 |
| 100 | 2,23 s | 2 s | +0,23 |
| 200 | 4,24 s | 4 s | +0,24 |
| 500 | 10,24 s | 10 s | +0,24 |
| 1.000 | 20,24 s | 20 s | +0,24 |
| 2.000 | 40,35 s | 40 s | +0,35 |

El tiempo es **exactamente** `ceil(K/W) × 1 s`: el techo es `W` tareas
simultáneas, y el resto espera en la cola. El sobrecosto es constante (~0,25 s)
y no crece con K: la cola no agrega costo por tarea. No es una degradación,
es el límite de diseño: **con 50 workers, el máximo son 50 tareas en
ejecución**, y por eso el pool es configurable.

#### b) Techo del event loop (`esperar` de 60 s, `WORKERS=K`: las K vivas a la vez)

| Tareas vivas | Admisión | RSS | `GET /estadisticas` p50 / p99 |
|-------------:|---------:|----:|------------------:|
| 1.000 | 916/s | 72 MB | 2,4 / 5,1 ms |
| 2.000 | 1.020/s | 79 MB | 2,5 / 3,2 ms |
| 5.000 | 904/s | 99 MB | 2,4 / 2,9 ms |
| 10.000 | 1.031/s | 134 MB | 2,4 / 3,0 ms |
| 20.000 | 1.042/s | 199 MB | 2,4 / 3,0 ms |

(Un servidor ocioso con 50 workers ocupa ~62 MB; el RSS de este experimento
crece ~6,6 KB por tarea viva.)

Con **20.000 corrutinas vivas a la vez, la latencia de la API no se movió**
(2,4 ms de p50 en todos). Ese es el punto de asyncio: una corrutina suspendida
cuesta unos KB de memoria y nada de CPU, no un hilo con su stack de 8 MB. El
techo acá no es el loop sino la **memoria** (lineal en K), y el ritmo de
admisión, que es el límite que sigue.

#### c) Techo de CPU (`hashear` de ~1 s de CPU, 12 núcleos lógicos)

| Tareas | Total | Ideal con 12 núcleos | Secuencial sería |
|-------:|------:|------:|------:|
| 1 | 0,81 s | 1 s | 1 s |
| 6 | 0,98 s | 1 s | 6 s |
| 12 | 1,49 s | 1 s | 12 s |
| 24 | 2,47 s | 2 s | 24 s |
| 48 | 4,38 s | 4 s | 48 s |

(La calibración de "1 s de CPU" es aproximada, de ahí el 0,81 s de K=1.) Hasta
12 tareas el tiempo casi no crece: cada una va a su núcleo. A partir de ahí
crece en escalones de ~1 s por cada 12: **el techo son los núcleos**, no los
workers (había 48 workers libres). 48 tareas tardaron 4,4 s en vez de 48 s:
~11× de paralelismo real, que `asyncio` por sí solo no daría jamás.

#### d) Techo de conexiones (1 tarea `descargar` con K URLs de 1 s)

| URLs | Total | Ideal con límite 100 |
|-----:|------:|------:|
| 50 | 1,06 s | 1 s |
| 100 | 1,08 s | 1 s |
| 200 | 2,11 s | 2 s |
| 400 | 4,13 s | 4 s |
| 500 | 5,16 s | 5 s |

Aparece el escalón de `MAX_CONEXIONES=100`: las 500 URLs son concurrentes *en
intención*, pero el `TCPConnector` solo deja 100 sockets abiertos a la vez y
las demás esperan su turno. Es un límite deliberado (no agotar descriptores
de archivo ni el servidor remoto), no un defecto, y se sube con la variable.

#### e) 10.000 tareas de golpe (configuración por defecto: 10 workers, cola de 5.000)

| Resultado | Valor |
|-----------|-------|
| Aceptadas (`202`) | 6.700 |
| Rechazadas (`503`) | 3.300 |
| Tiempo en enviar las 10.000 | 8,9 s → **1.119 `POST`/s** |
| Pendientes al terminar de enviar | 5.000 (cola llena) |
| RSS del servidor | 72 MB |
| `GET /estadisticas` durante la ráfaga | p50 1,9 ms, **p99 216 ms**, máx 257 ms |

**Interpretación.** Las 6.700 aceptadas son las 5.000 que caben en la cola más
las ~1.700 que los workers fueron sacando mientras llegaban. El sistema **no se
cae**: rechaza con `503` + `Retry-After` en lugar de crecer sin límite. Lo
primero que se satura es el **ritmo de admisión** y no el pool ni la memoria:
corriendo el servidor sobre un solo hilo, uvicorn + FastAPI + pydantic
agotan un núcleo (medido: 102 % de CPU del servidor frente a 43 % del
cliente) en ~1.100 requests por segundo. Y como esas requests compiten por
el mismo loop, hasta un `GET` trivial pasa de 2 ms a ~200 ms de cola.

#### Conclusión de la medición 3

No hay *un* techo; hay uno por recurso, y conviene saber cuál es cuál:

| Recurso | Techo | Señal |
|---------|-------|-------|
| Tareas I/O simultáneas | `WORKERS` | Total = `ceil(K/W) × t` |
| Conexiones HTTP salientes | `MAX_CONEXIONES` (100) | Escalones de 100 URLs |
| Tareas CPU simultáneas | Núcleos (`CPU_WORKERS`) | Escalones de 12 tareas |
| Corrutinas vivas | Memoria (~6,6 KB c/u) | RSS lineal; latencia plana hasta 20.000 |
| Ritmo de entrada de requests | 1 núcleo de Python (~1.100/s) | `GET` baja a ~200 ms en la ráfaga |
| Tareas pendientes | `MAX_COLA` (5.000) | `503` |

Para pasar el penúltimo habría que correr **varios procesos de uvicorn**, lo
cual rompe la cola y el estado en memoria compartidos (ver limitaciones).

---

## 5. Conceptos del curso aplicados

- **Productor-consumidor (clase 11).** Los endpoints producen ids en la cola
  y el pool de workers los consume ([app/gestor.py](app/gestor.py), `encolar` y
  `_worker`). Es el mismo patrón de la clase con threads, pero con corrutinas:
  acá no hace falta `Lock` porque el event loop no interrumpe entre `await`s.
  Además `Queue(maxsize=...)` agrega contrapresión: cuando la cola se llena,
  el productor (el `POST`) recibe el rechazo en vez de acumular sin límite.
- **Sockets TCP y servidores concurrentes (clases 13-14).** `uvicorn` es un
  servidor TCP que atiende miles de clientes en un hilo, el problema que en
  esas clases se resolvía con un hilo o proceso por conexión. `aiohttp`, del
  lado cliente, abre sockets TCP no bloqueantes: `descargar` tiene 100 en
  simultáneo con un solo hilo.
- **I/O multiplexing (clase 17).** Es lo que hay debajo del loop: en Linux
  `asyncio` usa `epoll` para preguntarle al kernel cuáles de los sockets
  registrados están listos. Cuando 500 descargas esperan a la vez (medición 1)
  el hilo está dormido en una sola llamada a `epoll_wait`, no en 500.
- **De `yield` a asyncio (clase 18).** `await` en `esperar`, `_bajar` o
  `_worker` es una suspensión de corrutina: el generador cede el control al
  loop, que lo retoma cuando el evento que espera ocurre. `asyncio.Task` es
  el objeto que va llamando a `send()` por nosotros. Y `time.sleep` en una
  corrutina es el bug que no cede nunca (medición 2, modo `bloqueante`).
- **HTTP y FastAPI (clase 19).** [app/api.py](app/api.py): rutas, códigos de
  estado con semántica (`202` Accepted: "lo recibí, aún no lo hice"; `409`,
  `404`, `503` con `Retry-After`), validación por pydantic con unión
  discriminada por `tipo`, e inyección de dependencias (`Depends`).
- **Asyncio en red y avanzado (clases 20-21).** `asyncio.timeout`,
  `Task.cancel()`/`CancelledError`, `TaskGroup`, `asyncio.wait` y
  `run_in_executor` están en `Gestor._correr`/`_ejecutar` y en
  [app/tareas.py](app/tareas.py). La cancelación cooperativa (que solo ocurre
  en un `await`) es la razón de partir el `hashear` en trozos.
- **`concurrent.futures` (clase 22).** `ProcessPoolExecutor` vs
  `ThreadPoolExecutor`, y la razón de elegir el primero para CPU (medición 2).
- **Bloque 0.** *Context managers*: `async with` para sesiones, conexiones,
  `TaskGroup` y `asyncio.timeout`, y `@asynccontextmanager` para el `lifespan`
  que abre y cierra el sistema entero. *Decoradores*: las rutas de FastAPI.
  *Generadores*: la base conceptual de las corrutinas (clase 18).
- **Señales (curso de procesos).** SIGTERM/SIGINT para el apagado ordenado,
  `kill` por `docker stop`, y por qué en `Dockerfile` el `CMD` va en forma
  *exec*: así Python es PID 1 y recibe la señal directamente en lugar de un
  shell intermedio.

---

## 6. Limitaciones conocidas

- **Un solo proceso de uvicorn.** El estado vive en memoria de un proceso, así
  que no se puede escalar horizontalmente sin sacar la cola y el estado a un
  servicio externo (Redis, un broker). Es la restricción del enunciado, pero es
  el techo real de la medición 3e.
- **El estado no se restaura al arrancar**, solo se reporta el último volcado.
  Un `kill -9` o un corte de luz pierde todo; el volcado solo cubre el apagado
  ordenado. Las tareas `pendiente` al apagar no se re-encolan.
- **Las tareas terminadas no se purgan nunca**: el diccionario crece sin tope
  (~6,6 KB por tarea, más lo que pese el resultado). Un servicio real
  necesitaría TTL o un máximo.
- **Cancelar CPU-bound no es instantáneo en el cómputo**: el trozo en curso
  termina y se descarta (≤ ~0,1 s de CPU desperdiciada por defecto).
- **En `HASH_MODO=bloqueante` no se puede cancelar ni vencer por timeout**,
  porque el loop está ocupado y no procesa nada. Existe solo para medir.
- **Una tarea sacada de la cola por un worker que muere se marca `fallida`**,
  no se reintenta.
- **Cancelar una tarea `pendiente` no libera su slot de cola** hasta que un
  worker la desencola (es inmediato si hay workers libres).
- **Sin autenticación ni límite por cliente** (eso es una extensión opcional),
  y `descargar` acepta cualquier URL `http(s)`, incluida la red interna del
  servidor: un servicio expuesto necesitaría una lista de destinos permitidos
  (riesgo de SSRF).
- **`/tareas` recorre el diccionario** para filtrar; con muchísimas tareas sería
  O(n) en el loop.
- **El ritmo de admisión** (~1.100 `POST`/s) está acotado por un núcleo.
- **Alcance de las mediciones**: una sola máquina (WSL2), cliente y servidor
  juntos, una corrida para las mediciones 2 y 3. Los órdenes de magnitud y la
  forma (escalones, órdenes de magnitud entre modos) son robustos; las décimas de segundo no.
  Los números con internet real son ruidosos.

---

## 7. Lo que aprendí

Lo que más me cambió la forma de pensar es la diferencia entre *esperar* y
*calcular*. Antes tenía la idea vaga de que "asyncio hace las cosas en
paralelo", y la medición 2 me lo desarmó: con el hash dentro del loop, un
simple `GET` tardó 6 segundos en responder, y no porque el servidor estuviera
ocupado "en paralelo" sino porque no había nada en paralelo: había un hilo y
estaba ocupado. Asyncio no hace más rápido nada; solo permite que mientras una
cosa *espera*, otra avance. Con el CPU no hay espera que aprovechar.

La segunda sorpresa fue lo poco que cuesta una corrutina comparada con lo que
pensaba: 20.000 tareas vivas con la latencia plana y ~200 MB. Y al revés, lo
que sí es caro: el servidor se saturó en ~1.100 requests por segundo, un
núcleo, mucho antes que cualquier cosa del pool. El techo no estaba donde yo
esperaba.

También aprendí que la cancelación limpia es un tema de diseño y no de
agregar un `.cancel()`. Fue testeando que encontré un bug real: si un worker
moría después de sacar una tarea de la cola, la tarea quedaba `pendiente`
para siempre. Y que partir el hash en trozos no fue una optimización sino lo
que hace posible que `DELETE` y el timeout funcionen sobre una tarea que corre
en otro proceso. Si tuviera que hacerlo de nuevo, empezaría por escribir el
test de cancelación antes que las tareas.
