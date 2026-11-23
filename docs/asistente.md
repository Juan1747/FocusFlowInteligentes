# Asistente en lenguaje natural — módulo de Juan David Cárdenas

Dos funciones que le ponen lenguaje al agente de FocusFlow sin quitarle sus garantías:

1. **Crear tareas hablando** (`POST /interpretar`). "Tengo el taller de cálculo para el jueves a las 6, me toma como 2 horas" se convierte en una tarea estructurada. El usuario la revisa en el formulario antes de guardarla.
2. **Chat del agente con herramientas** (`POST /asistente`). "¿Me alcanza el tiempo esta semana?", "¿en qué me equivoco más?", "agrégame 2 horas el martes en la noche". El agente consulta los datos reales y **propone** cambios que el usuario confirma.

| Archivo | Qué contiene |
|---|---|
| `asistente.py` | Intérprete (LLM y reglas), proveedores, herramientas, loop del agente y endpoints |
| `prueba_asistente.py` | 14 pruebas, sin llamar a ningún modelo real (proveedor simulado y SQLite en memoria) |
| `evaluacion_interpretar.py` | Comparación reglas / Claude / Ollama sobre las mismas frases |
| `datos/frases_interpretar.json` | 80 frases con su respuesta esperada (ajuste, prueba, ciego) |
| `docs/evaluacion_interpretar.md` | Resultados generados por el script |
| `frontend/src/pantallas/Asistente.jsx` | Pantalla del chat con tarjetas de propuesta |
| `frontend/src/pantallas/NuevaTarea.jsx` | Cuadro "escríbela como se la dirías a alguien" |

---

## 1. El principio: el LLM propone, el sistema decide

| Parte | Lo hace | Por qué |
|---|---|---|
| Entender la frase o la pregunta | **LLM** | Es un problema de lenguaje: jerga, fechas relativas, matices ("no es tan urgente") |
| A qué hora va cada tarea | **Planificador (CSP)** | Un LLM no garantiza que no haya solapes ni que se respeten los bloques |
| Cuánto va a durar | **Estimador** | Sale del historial real del usuario, no de lo que "suena razonable" |
| Validar lo que propone el LLM | **Esquemas de la API** | Tipo dentro del catálogo, fecha real y futura, sin solapes, prioridad 1–3 |
| Aplicar un cambio | **El usuario** | Las propuestas son tarjetas con "Confirmar"; el agente no escribe en la base |

Es lo que la semana 10 llama decidir **qué no delegar a un modelo**. Además, **sin LLM la app sigue funcionando**: `/interpretar` usa un intérprete por reglas (que también es la línea base de la evaluación) y el formulario sigue ahí.

---

## 2. Cómo funciona

### Interpretar (`POST /interpretar`)

```
frase ──► LLM con salida estructurada (JSON Schema) ──► normalizar_interpretacion() ──► formulario
            │ falla o no hay modelo                       (valida tipo, fecha, hora,
            └──► interpretar_por_reglas() ───────────────► prioridad, duración)
```

- El prompt incluye un **calendario de los próximos 21 días**, para que el modelo no tenga que calcular qué fecha es "el jueves" (los LLM se equivocan en eso).
- La salida está restringida por un JSON Schema. Con Claude se usa `output_config.format`, y con Ollama `format`. Se usa el mismo esquema en los dos.
- Lo que no se entendió no se inventa: queda en `faltantes` y el formulario lo resalta. Si falta la fecha límite, el campo queda vacío para que el usuario la elija.

Respuesta:

```json
{"tarea": {"nombre": "Taller de Cálculo", "tipo": "taller", "limite": "2026-11-26T18:00:00",
           "prioridad": 3, "estimacion_usuario_min": 120},
 "faltantes": [], "fuente": "claude", "modelo": "claude-haiku-5-5", "aviso": null}
```

### El agente conversacional (`POST /asistente`)

```
mensaje ──► LLM ──► ¿pide herramientas? ── sí ──► ejecutar (lee la base / arma propuesta) ──┐
             ▲                                                                              │
             └──────────────────────────── resultado (o error para que corrija) ◄───────────┘
                     no ──► respuesta en texto + propuestas pendientes + qué consultó
```

El loop lo escribimos nosotros, sin el Tool Runner del SDK ni Managed Agents, por dos razones:
- El mismo loop tiene que servir para Claude, para Ollama y para el modelo simulado de las pruebas.
- Las herramientas leen la base local de la app.

Tiene un máximo de 8 rondas de herramientas por mensaje.

| Herramienta | Qué hace | Escribe |
|---|---|---|
| `ver_tareas` | tareas con id, tipo, límite, prioridad, estimación y estado | no |
| `ver_bloques_libres` | tiempo libre declarado por día | no |
| `ver_agenda` | agenda desde ahora con **el mismo** `_peticion_plan` y `planificar` de `main.py` | no |
| `ver_historial` | por tipo: estimado medio, real medio y factor real/estimado | no |
| `proponer_crear_tarea` | arma una tarea validada como `TareaCreate` | **no**: queda como propuesta |
| `proponer_editar_tarea` | cambios validados como `TareaUpdate`, con el "antes → después" | **no** |
| `proponer_agregar_bloque_libre` | suma un bloque **conservando** los del día y valida solapes | **no** |

Cuando el usuario confirma una tarjeta, la interfaz llama a los endpoints de siempre: `POST /tareas`, `PATCH /tareas/{id}` o `POST /bloques-libres`. No existe un camino nuevo para escribir en la base. Si una propuesta no pasa la validación (por ejemplo, un bloque que se solapa o una fecha que ya pasó), el error vuelve al modelo como `is_error` para que corrija o le pregunte al usuario.

---

## 3. Proveedores y cómo activarlos

`FOCUSFLOW_LLM` = `auto` (por defecto) · `claude` · `ollama` · `reglas`

| Proveedor | Interpretar | Chat | Costo | Cuándo |
|---|---|---|---|---|
| **Claude** | Claude Haiku 5.5 (`claude-haiku-5-5`) | Claude Opus 5.5 (`claude-opus-5-5`) | de pago, ver abajo | si hay `ANTHROPIC_API_KEY` |
| **Ollama** | `qwen2.5:3b` (local) | `qwen2.5:3b` | gratis, corre en el equipo | si Ollama está corriendo |
| **Reglas** | palabras clave y fechas | no disponible | gratis | sin ninguno de los dos |

En `auto` se elige Claude si hay credenciales; si no, Ollama si está corriendo; si no, reglas. Se recalcula cada 30 s, así que encender Ollama no exige reiniciar el backend. `GET /asistente/estado` dice qué se está usando.

### Opción gratuita: Ollama (recomendada para desarrollar y como respaldo en la demo)

1. Instalar Ollama desde https://ollama.com/download (Windows, macOS o Linux).
2. En una terminal: `ollama pull qwen2.5:3b` (unos 2 GB; cabe en una GPU de 4 GB).
3. Ollama queda corriendo en segundo plano. Reiniciar o esperar 30 s y el backend lo detecta.

Otro modelo: `FOCUSFLOW_OLLAMA_MODELO=qwen2.5:7b` (más preciso y más lento). Otro servidor: `OLLAMA_HOST=http://host:11434`.

### Opción Claude

```bash
pip install anthropic
# PowerShell:  $env:ANTHROPIC_API_KEY = "tu-clave"     |  bash:  export ANTHROPIC_API_KEY=tu-clave
uvicorn main:app --reload
```

La clave **nunca** se escribe en el código ni se sube al repositorio. Los precios por millón de tokens (entrada / salida) son $0,10 / $0,50 para Haiku 5.5 y $4 / $20 para Opus 5.5. Una interpretación cuesta una fracción de centavo. Un mensaje de chat con varias herramientas puede costar unos centavos de dólar con Opus (es un estimado, no una medición). El chat activa los *fallbacks* del lado del servidor: si un clasificador de seguridad rechaza una solicitud, la API la reintenta sola con otro modelo.

---

## 4. Evidencia

`python evaluacion_interpretar.py --guardar` compara los métodos disponibles sobre las mismas frases. Mide la exactitud por campo y la de **tarea completa**, que exige todos los campos bien. Todas las salidas pasan por la misma validación que usa la app.

**Los tres conjuntos de frases y por qué hay tres:**

| Conjunto | Frases | Uso |
|---|---:|---|
| `ajuste` | 20 | afinar reglas y prompt |
| `prueba` | 30 | **no sirve para medir las reglas**: las reglas se escribieron después de leer estas frases, así que se sobreajustaron (sacan 100 %) |
| `ciego` | 30 | escrito con las reglas y el prompt **ya congelados**; frases más desordenadas ("pa mañana", "1 de dic", "2 horitas"). Es el que se reporta |

Resultados actuales (conjunto ciego, ver `docs/evaluacion_interpretar.md`):

| Método | Tipo | Fecha | Hora | Prioridad | Estimación | Nombre | **Tarea completa** |
|---|---:|---:|---:|---:|---:|---:|---:|
| Reglas (línea base) | 93 % | 93 % | 97 % | 93 % | 97 % | 93 % | **73 %** |
| Claude Haiku 5.5 | pendiente: necesita `ANTHROPIC_API_KEY` | | | | | | |
| Ollama `qwen2.5:3b` | pendiente: necesita Ollama instalado | | | | | | |

Lectura honesta:
- **73 % de tareas completas** significa que con reglas más de 1 de cada 4 frases necesita corrección manual. Las fallas son justo lo que el lenguaje natural tiene de difícil: jerga ("2 horitas", "1 de dic"), matices ("no es *muy* urgente", "crítico") y ambigüedad ("el proyecto de emprendimiento" no es programación).
- Después de ver el conjunto ciego se corrigió **un** error de la expresión regular ("a las 11 de la noche" se leía como 11:00). Antes de esa corrección, el resultado era 70 %. Las reglas no se volvieron a tocar.
- Las frases las escribió el mismo equipo. Lo ideal es que alguien que no conoce las reglas escriba 30 más; se agregan como otro conjunto y se reporta ese.
- El chat se evalúa con las pruebas automáticas (loop, herramientas, validación, propuestas, límites) y con un guion de demo. No hay una métrica de "calidad de respuesta" porque no tendría un ground truth honesto.

---

## 5. Seguridad y privacidad

- **Nada se escribe sin confirmación.** Las herramientas `proponer_*` solo devuelven propuestas validadas; el agente no tiene ninguna herramienta que escriba en la base.
- **Qué datos salen del equipo:** con Claude, la frase o la conversación y lo que devuelven las herramientas (nombres de tareas, fechas, bloques, historial por tipo). Con Ollama, nada sale del equipo. Esto se dice en la sustentación.
- **Inyección de instrucciones:** el texto de las tareas es del mismo usuario, y aun si una tarea tuviera instrucciones raras, lo peor que puede pasar es una propuesta que el usuario ve y descarta.
- **Sin claves en el código:** se leen de variables de entorno.

---

## 6. Preguntas probables del jurado

| Pregunta | Respuesta corta |
|---|---|
| ¿Para qué un LLM si ya tenían un CSP? | Para lo que el CSP no hace: entender lenguaje. El CSP sigue decidiendo las horas; el LLM nunca las decide. |
| ¿Y si el LLM se equivoca? | Todo pasa por validación, lo que no entiende queda vacío y el usuario confirma. Si el modelo falla, se usa el intérprete por reglas. |
| ¿Cómo saben que el LLM aporta? | Se compara contra una línea base sin LLM sobre frases que no se usaron para ajustar (sección 4). |
| ¿Por qué no un agente que haga todo solo? | Porque un error cambiaría datos reales sin que el usuario lo vea. Proponer y confirmar es la decisión de diseño de la semana 10. |
| ¿Por qué Haiku para interpretar y Opus para el chat? | Extraer campos de una frase es simple y frecuente: modelo barato. El chat razona con varias herramientas: modelo más capaz. |
| ¿Funciona sin internet? | Sí, con Ollama local; y sin ningún modelo, con reglas (sin chat). |

---

## 7. Límites

| Límite | Mitigación |
|---|---|
| El modelo local de 3 B se equivoca más que Claude, sobre todo encadenando herramientas | probar `qwen2.5:7b`; medirlo con la evaluación antes de la demo |
| El agente no puede fijar la hora de una tarea | es a propósito: ofrece cambiar prioridad, límite o tiempo libre |
| Las frases de evaluación las escribió el equipo | agregar un conjunto escrito por otras personas |
| Solo se envían los últimos 20 mensajes del chat | suficiente para conversaciones de planificación; "Nueva conversación" reinicia |
| La conversación vive en el navegador (sessionStorage) | no se guarda en el servidor; se pierde al cerrar la pestaña |
