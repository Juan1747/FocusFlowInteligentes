# FocusFlow

Proyecto final de Introducción a los Sistemas Inteligentes (UNAL). **FocusFlow** es un planificador de tareas que aprende cuánto tardan de verdad las tareas del usuario, arma la agenda respetando bloques libres, plazos y descansos, y la rehace automáticamente cuando algo se demora, explicando en una frase qué cambió y por qué.

## Los tres módulos

El sistema son tres módulos, uno por integrante. Ninguno llama a otro directamente: todo pasa por el agente, lo que permite trabajar en paralelo contra un **contrato de API** común (`schemas.py`).

| Módulo | Responsable | Historias | Qué hace | Estado |
|---|---|---|---|---|
| **Estimador y evaluación** | Alvaro | US-A1 a US-A8 | Aprende la duración por tipo de tarea, con incertidumbre, y mezcla la estimación del usuario con la historia | Lógica de estimación y adaptación lista (US-A1–A3, A7) |
| **Planificador e interfaz** | Juan David Cárdenas | US-B1 a US-B7 | Resuelve un CSP (OR-Tools CP-SAT): ubica tareas en bloques libres sin solaparlas, respetando plazos y descansos | Pendiente |
| **Agente y backend** | Juan David Chacón | US-C1 a US-C7 | API en FastAPI + SQLite, loop del agente, explicación y demo | Funcionando; estimador real conectado, planificador en mock |

> **Estado de la integración:** el **estimador** de Alvaro está conectado al backend (`/estimar` y `/estimador/actualizar` usan `estimador.py`, no mocks). El **planificador** (`/planificar`) todavía devuelve datos fijos hasta que Cárdenas lo entregue. Ver [Estimador adaptativo](#estimador-adaptativo-us-a1-a-us-a8).

## Estructura

### Estimador (Alvaro)

| Archivo | Qué contiene | Historias |
|---|---|---|
| `estimador.py` | Estimador bayesiano por tipo de tarea + adaptador al contrato | US-A1, US-A2, US-A3, US-A7 |
| `prueba_estimador.py` | Script de convergencia: evidencia de que aprender mejora la predicción | US-A2, US-A3 |
| `docs/estimador.md` | Documentación del módulo, API y preguntas del jurado | — |

### Agente y backend (Chacón)

| Archivo | Qué contiene | Historias |
|---|---|---|
| `database.py` | Conexión a SQLite, sesiones y `Base` de los modelos | US-C2 |
| `models.py` | Tablas `tareas`, `bloques_libres` y `registros` | US-C2, US-C3, US-C4 |
| `schemas.py` | Esquemas Pydantic: tareas, bloques libres y el contrato de API | US-C1, US-C2, US-C3, US-C4, US-C6 |
| `main.py` | Endpoints, mocks del contrato y loop del agente | US-C1 a US-C6 |
| `explicacion.py` | Genera el texto que explica qué cambió en la agenda | US-C6 |
| `demo.py` | Script que reproduce el escenario de la sustentación | US-C7 |

## Requisitos e instalación

Python 3.9 o superior.

El **estimador** (`estimador.py`, `prueba_estimador.py`) no necesita dependencias externas: usa solo la biblioteca estándar. Se puede ejecutar y probar sin instalar nada.

El **backend y agente** necesita FastAPI, Pydantic v2 y SQLAlchemy:

```bash
pip install fastapi uvicorn sqlalchemy requests
```

## Cómo ejecutar

### Estimador (sin servidor)

```bash
python prueba_estimador.py
```

Corre la demostración de convergencia del estimador. Ver [Estimador adaptativo](#estimador-adaptativo-us-a1-a-us-a8).

### Backend y agente

```bash
uvicorn main:app --reload
```

- API: http://127.0.0.1:8000
- Documentación interactiva: http://127.0.0.1:8000/docs

La base de datos `focusflow.db` se crea sola en la carpeta del proyecto al arrancar.

## Endpoints

### Tareas (US-C2)

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/tareas` | Crea una tarea |
| `GET` | `/tareas` | Lista las tareas (filtro opcional `?estado=`) |
| `GET` | `/tareas/{id}` | Consulta una tarea |
| `PATCH` | `/tareas/{id}` | Edita los campos enviados |

```json
// POST /tareas
{"nombre": "Lectura de Estocásticos", "tipo": "lectura",
 "limite": "2026-11-20T23:59", "prioridad": 2, "estimacion_usuario_min": 60}
```

- `nombre` es obligatorio.
- `estado` puede ser `pendiente`, `en_progreso` o `terminada`; toda tarea nace `pendiente`.

### Bloques libres (US-C3)

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/bloques-libres` | Registra los bloques libres de un día |
| `GET` | `/bloques-libres` | Lista los bloques (filtro opcional `?dia=2026-11-17`) |

```json
// POST /bloques-libres
{"dia": "2026-11-17", "bloques": [{"inicio": "18:00", "fin": "21:00"}]}
```

Registrar un día **reemplaza** los bloques que ese día ya tuviera. Se rechazan rangos invertidos y bloques que se solapan.

### Terminar una tarea y replanificar (US-C4, US-C5, US-C6)

`POST /tareas/{id}/terminar`

```json
// entrada: horas reales de inicio y fin
{"inicio": "2026-11-17T14:00", "fin": "2026-11-17T17:00"}
```

Devuelve la nueva agenda con la explicación (misma forma que la salida de `/planificar`). Si la tarea ya estaba terminada responde `409`.

## Contrato de API entre módulos (US-C1)

### `POST /estimar` — estimador

```json
// entrada
{"tipo": "programacion", "estimacion_usuario_min": 90}

// salida
{"duracion_esperada_min": 142, "incertidumbre_min": 38,
 "confianza": "media", "n_observaciones": 7}
```

### `POST /estimador/actualizar` — estimador

```json
// entrada
{"tipo": "programacion", "duracion_real_min": 180}

// salida
{"media_min": 148, "incertidumbre_min": 41, "n_observaciones": 8}
```

### `POST /planificar` — planificador

```json
// entrada
{"tareas": [{"id": 1, "tipo": "programacion", "duracion_min": 142,
             "limite": "2026-11-20T23:59", "prioridad": 3}],
 "bloques_libres": [{"inicio": "2026-11-17T18:00", "fin": "2026-11-17T21:00"}],
 "descanso_min": 15}

// salida
{"factible": true,
 "agenda": [{"tarea_id": 1, "inicio": "2026-11-17T18:00", "fin": "2026-11-17T20:22"}],
 "en_riesgo": [{"tarea_id": 3, "holgura_min": -25}],
 "explicacion": ""}
```

### Reglas del contrato

- Todos los minutos son **enteros**; hay que redondear antes de devolver.
- `confianza` admite `"alta"`, `"media"` o `"baja"`.
- `tipo` es texto libre.
- Duraciones mayores que 0, `prioridad` mínimo 1, y en cada bloque `fin` posterior a `inicio`.
- `descanso_min` es opcional; vale 15 si no se envía.
- Fechas en ISO 8601, hora local, sin zona horaria. Se acepta `2026-11-17T18:00`; las respuestas llevan segundos (`2026-11-17T18:00:00`).
- `explicacion` es opcional: el planificador no la envía. Solo llega con texto en la respuesta de `/tareas/{id}/terminar`.

## Estimador adaptativo (US-A1 a US-A8)

El estimador aprende cuánto dura cada **tipo** de tarea y predice tareas nuevas mezclando la estimación del usuario con la historia aprendida, ponderando por cantidad de datos:

```
d_hat = w * mu + (1 - w) * e        con    w = n / (n + k)
```

Con 0 observaciones la predicción es la estimación del usuario; con muchas, se acerca a la media real aprendida. La media y la varianza se actualizan incrementalmente con el método de Welford, y de la varianza sale un nivel de confianza (`alta` / `media` / `baja`).

Uso directo de la clase:

```python
from estimador import Estimador

est = Estimador()
est.actualizar("programacion", 180)        # aprende de una tarea terminada
pred = est.predecir("programacion", 90)    # predice la próxima
print(pred.duracion_esperada_min, pred.confianza, pred.n_observaciones)
```

### Cómo quedó conectado al backend

`estimador.py` trae un adaptador que produce los objetos exactos del contrato (`schemas.py`), con minutos **enteros**. Así está cableado en `main.py` (reemplazó los mocks de `/estimar` y `/estimador/actualizar`):

```python
from estimador import Estimador, estimar_contrato, actualizar_contrato

estimador = Estimador()

@app.post("/estimar", response_model=schemas.EstimarResponse)
def estimar(payload: schemas.EstimarRequest):
    return estimar_contrato(estimador, payload)

@app.post("/estimador/actualizar", response_model=schemas.ActualizarEstimadorResponse)
def actualizar_estimador(payload: schemas.ActualizarEstimadorRequest):
    return actualizar_contrato(estimador, payload)
```

El loop del agente no cambia: ya lee `media_min`, `incertidumbre_min` y `n_observaciones` de la respuesta.

Detalle completo del módulo (fórmulas, umbrales de confianza, mapa de nombres y preguntas del jurado) en [`docs/estimador.md`](docs/estimador.md).

## El loop del agente (US-C5)

Al terminar una tarea, `POST /tareas/{id}/terminar` guarda el registro y ejecuta el ciclo completo, dejando un log por paso en la consola de uvicorn:

1. **Percibir.** Recibe la duración real y calcula la agenda vieja: el plan que había si la tarea hubiera durado lo estimado.
2. **Actualizar el modelo del mundo.** Envía la duración real al estimador.
3. **Recalcular.** Pide una estimación nueva para cada tarea pendiente.
4. **Decidir.** Pide una agenda nueva al planificador con los bloques libres que quedan desde la hora real de fin.
5. **Explicar y publicar.** Compara las dos agendas, genera la explicación y devuelve la agenda nueva.

Descripción PEAS: el entorno son las tareas y el tiempo libre del usuario; los sensores, los eventos de fin de tarea; los actuadores, la agenda propuesta y la explicación; la medida de desempeño, el porcentaje de tareas entregadas a tiempo y el error de estimación.

## La explicación (US-C6)

`explicacion.py` expone `generar_explicacion(tarea_terminada, duracion_estimada, duracion_real, agenda_vieja, agenda_nueva, nombres=None)`. Usa plantillas sobre el diff de las dos agendas, sin modelo de lenguaje. Detecta:

- si la tarea tardó más, menos o lo estimado;
- qué tareas cambiaron de hora de inicio;
- qué tareas entraron o salieron de riesgo;
- qué tareas dejaron de caber en la agenda;
- si la agenda nueva no es factible.

Ejemplo de salida:

> "Taller de Cálculo" tardó 3 h en vez de 1 h 30 min (1 h 30 min más de lo estimado). Por eso moví "Lectura de Estocásticos" de las 7:00 pm a las 8:30 pm. Ahora está en riesgo "Proyecto de Sistemas Inteligentes" (le faltan 25 min para cumplir su plazo).

## Demo (US-C7)

Con el servidor corriendo, en otra terminal:

```bash
python demo.py
```

El script:

1. Crea 3 tareas.
2. Declara el bloque libre de hoy (14:00 a 22:00).
3. Termina la primera tarea con el doble del tiempo estimado.
4. Imprime la explicación, la nueva agenda y las tareas en riesgo.

Cada ejecución crea 3 tareas y un registro nuevos. Para que la tarea atrasada sea la #1, arranca con una base limpia (borra `focusflow.db`). **No corras la demo sobre la base donde se registran las tareas reales del equipo**, porque ensucia la tabla `registros` que usa la evaluación.

## Base de datos

| Tabla | Columnas |
|---|---|
| `tareas` | id, nombre, tipo, limite, prioridad, estimacion_usuario_min, estado |
| `bloques_libres` | id, inicio, fin |
| `registros` | id, tarea_id, tipo, estimacion_min, duracion_real_min, terminada_en |

SQLAlchemy no añade columnas a tablas existentes. Si tu `focusflow.db` es anterior al campo `nombre`, bórrala o ejecuta:

```bash
sqlite3 focusflow.db "ALTER TABLE tareas ADD COLUMN nombre VARCHAR NOT NULL DEFAULT 'Sin nombre';"
```

## Limitaciones conocidas y pendientes

- **Mocks.** El estimador real (`estimador.py`) ya está cableado en `main.py`: `/estimar` y `/estimador/actualizar` usan datos reales y el log del agente refleja la media e incertidumbre aprendidas. El **planificador** sigue siendo un mock (datos fijos): mientras `/planificar` no sea real, la agenda vieja y la nueva salen iguales y la explicación dice "El resto de la agenda no cambió".
- **Un solo usuario.** No hay tabla ni columna de usuario; los bloques libres no se asocian a nadie.
- **Sin endpoint de "empezar".** Las horas de inicio y fin se envían juntas al terminar la tarea.
- **Sin endpoint para consultar la agenda actual.** La agenda solo se devuelve al terminar una tarea.
- **Margen de incertidumbre.** El agente pasa al planificador `duracion_esperada_min` sin margen; falta decidir quién lo suma (estimador, planificador o agente).
- **Descanso fijo.** El agente siempre pide 15 minutos de descanso entre tareas.
- **Ruta de la base fija.** Está escrita en `database.py`; para usar otra base hay que cambiarla ahí.