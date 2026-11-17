# FocusFlow — Backend y agente

Backend del proyecto final de Introducción a los Sistemas Inteligentes (UNAL). FocusFlow es un planificador de tareas que aprende cuánto tardan de verdad las tareas del usuario y rehace la agenda cuando algo se demora.

Este repositorio contiene el módulo **Agente y backend** (historias US-C1 a US-C7): la API en FastAPI, la base de datos SQLite, el loop del agente, la explicación de cada replanificación y el script de demo.

> **Estado actual:** los endpoints del estimador y del planificador (`/estimar`, `/estimador/actualizar`, `/planificar`) devuelven datos fijos que cumplen el contrato. Se reemplazan cuando se conecten los módulos reales de Alvaro (estimador) y Cárdenas (planificador).

## Estructura

| Archivo | Qué contiene | Historias |
|---|---|---|
| `database.py` | Conexión a SQLite, sesiones y `Base` de los modelos | US-C2 |
| `models.py` | Tablas `tareas`, `bloques_libres` y `registros` | US-C2, US-C3, US-C4 |
| `schemas.py` | Esquemas Pydantic: tareas, bloques libres y el contrato de API | US-C1, US-C2, US-C3, US-C4, US-C6 |
| `main.py` | Endpoints, mocks del contrato y loop del agente | US-C1 a US-C6 |
| `explicacion.py` | Genera el texto que explica qué cambió en la agenda | US-C6 |
| `demo.py` | Script que reproduce el escenario de la sustentación | US-C7 |

## Requisitos e instalación

Python 3.9 o superior y Pydantic v2.

```bash
pip install fastapi uvicorn sqlalchemy requests
```

## Cómo ejecutar

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

- **Mocks.** Mientras estimador y planificador devuelvan datos fijos, la agenda vieja y la nueva son iguales y la explicación siempre dice "El resto de la agenda no cambió".
- **Un solo usuario.** No hay tabla ni columna de usuario; los bloques libres no se asocian a nadie.
- **Sin endpoint de "empezar".** Las horas de inicio y fin se envían juntas al terminar la tarea.
- **Sin endpoint para consultar la agenda actual.** La agenda solo se devuelve al terminar una tarea.
- **Margen de incertidumbre.** El agente pasa al planificador `duracion_esperada_min` sin margen; falta decidir quién lo suma (estimador, planificador o agente).
- **Descanso fijo.** El agente siempre pide 15 minutos de descanso entre tareas.
- **Ruta de la base fija.** Está escrita en `database.py`; para usar otra base hay que cambiarla ahí.