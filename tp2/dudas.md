# Dudas que me quedaron

Cosas que me crucé haciendo el TP y que no terminé de resolver o de entender
del todo.

---

### 1. Cancelar trabajo CPU-bound que ya está corriendo en otro proceso

`DELETE` sobre un `hashear` cancela la corrutina al instante, pero el trozo
que ya estaba en un proceso del pool **sigue corriendo hasta terminar** (hasta
~0,1 s de CPU con el `HASH_CHUNK` por defecto). Lo resolví partiendo el
trabajo en trozos para que el desperdicio tenga un tope.

Lo que no sé es si hay una forma "correcta" de interrumpir de verdad una
función Python que está en un `ProcessPoolExecutor`. Leí que `Future.cancel()`
solo funciona si el trabajo todavía no empezó, y que la alternativa es manejar
los procesos a mano con `multiprocessing.Process` y `terminate()`. ¿Vale la
pena esa complejidad, o partir en trozos es lo que se hace en la práctica?

### 2. `signal.signal` encadenado con el handler de uvicorn

Para dejar de aceptar tareas en el instante de la señal envuelvo el handler
que uvicorn ya instaló (ver `_encadenar_senales` en `app/api.py`). Funciona,
pero depende de un detalle de implementación de uvicorn: que use
`signal.signal` y no `loop.add_signal_handler`. En versiones viejas hacía lo
segundo y mi truco no habría funcionado (por eso fijé la versión en
`requirements.txt`).

¿Es una mala práctica depender de eso? ¿Habría sido más prolijo manejar el
apagado entero yo mismo con `uvicorn.Server` y mis propios handlers?

### 3. ¿Cuándo conviene `TaskGroup` y cuándo `gather`?

En `descargar` usé `asyncio.TaskGroup` porque garantiza que si se cancela la
tarea madre, las hijas se cancelan **y se esperan** antes de salir. Con
`gather` entendí que la cancelación también se propaga, pero que ante la
primera excepción las demás hijas quedan corriendo si no uso
`return_exceptions=True`.

Mi duda es si hay algún caso real donde `gather` siga siendo mejor, más allá
de que funciona en Python < 3.11.

### 4. Memoria de las tareas terminadas

El diccionario `tareas` nunca se purga: cada tarea terminada se queda con su
resultado en memoria hasta que el proceso muere. Para este TP alcanza, pero
con un servicio de verdad habría que decidir una política (¿TTL?, ¿máximo de
N terminadas?) y no sé cuál es la convención. Además, si purgo, ¿qué pasa con
los contadores de `/estadisticas`?

### 5. Una tarea sacada de la cola por un worker que muere

Encontré, probando, que si un worker muere (excepción que escapa a todo)
justo después de sacar una tarea de la cola, esa tarea se perdía: quedaba
`pendiente` para siempre porque ya no estaba en la cola. Lo arreglé marcándola
como `fallida`, pero la alternativa más natural sería **reencolarla**. No lo
hice porque si el worker murió *por culpa de esa tarea*, reencolarla podría
matar a todos los workers en cadena. ¿Cómo se decide eso normalmente
(reintentos con tope, dead-letter queue)?
