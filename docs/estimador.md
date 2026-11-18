# Estimador adaptativo — módulo de Alvaro

Documentación del módulo **Estimador** de FocusFlow (historias US-A1 a US-A8).
Pensada para que cualquiera del equipo pueda entenderlo, defenderlo ante el jurado
y conectarlo con el backend sin leer todo el código.

- **Archivos:** `estimador.py` (lógica) y `prueba_estimador.py` (evidencia).
- **Dependencias:** ninguna externa para la lógica. Solo la biblioteca estándar
  (`math`, `dataclasses`). Pydantic/FastAPI solo hacen falta si se usa el adaptador
  al contrato, y se importan de forma perezosa.

---

## 1. Qué hace y por qué

El estimador aprende **cuánto dura de verdad cada tipo de tarea** (programación,
lectura, taller, estudio...) y lo usa para predecir la duración de tareas nuevas.

La clave es que no devuelve un promedio pelado: mezcla **lo que el usuario estima**
con **lo que la historia ha enseñado**, dándole más peso a la historia a medida que
hay más datos. Así no sobre-confía en pocos registros, y con el tiempo corrige los
sesgos del usuario (p. ej. alguien que siempre subestima programación).

### La fórmula de predicción (US-A3)

```
d_hat = w * mu + (1 - w) * e        con    w = n / (n + k)
```

| Símbolo | Significado |
|---|---|
| `e`  | estimación cruda del usuario (minutos) |
| `mu` | media de duración aprendida para ese tipo |
| `n`  | número de observaciones de ese tipo |
| `k`  | constante de suavizado (por defecto `5`) |
| `w`  | peso de la historia: `0` sin datos, tiende a `1` con muchos |

- `n = 0` → `w = 0` → la predicción **es** la estimación del usuario.
- `n = k` → `w = 0.5` → usuario e historia pesan igual.
- `n` grande → `w → 1` → la predicción se acerca a la media real aprendida.

### Actualización incremental (US-A2) — método de Welford

Al terminar una tarea con duración real `d`, se actualizan media y varianza en una
sola pasada, sin guardar el historial completo:

```
n  <- n + 1
mu <- mu + (d - mu) / n
M2 <- M2 + (d - mu_viejo) * (d - mu_nuevo)
varianza = M2 / (n - 1)
```

### Nivel de confianza (US-A7)

De la desviación estándar se deriva una etiqueta legible, usando el **coeficiente de
variación** (`desviación / media`, adimensional) más un mínimo de datos:

| Condición | Confianza |
|---|---|
| `n < 3` | `baja` |
| coef. variación > 0.5 | `baja` |
| coef. variación > 0.25, **o** `n < 8` | `media` |
| resto (`n ≥ 8` y poca dispersión) | `alta` |

El mínimo de observaciones evita declarar "alta" confianza cuando, por azar, dos o
tres tareas salieron casi idénticas.

---

## 2. API del módulo

### Clase `Estimador`

```python
from estimador import Estimador

est = Estimador(k=5)                      # k opcional, por defecto 5

est.actualizar("programacion", 180)        # aprende de una tarea terminada (US-A2)
pred = est.predecir("programacion", 90)    # predice la próxima (US-A3)

pred.duracion_esperada_min   # float, la predicción d_hat
pred.incertidumbre_min       # float, desviación estándar aprendida
pred.confianza               # "alta" | "media" | "baja" (US-A7)
pred.n_observaciones         # cuántos datos respaldan la predicción
pred.peso_historia           # el w usado (solo para depurar/explicar)

est.tipos_conocidos()        # tipos con al menos una observación
```

| Método | Para qué | Historia |
|---|---|---|
| `actualizar(tipo, duracion_real)` | registra una duración real y aprende | US-A2 |
| `predecir(tipo, estimacion_usuario)` | predice duración mezclando usuario e historia | US-A3 |
| `tipos_conocidos()` | lista los tipos ya observados | US-A1 |

Ambos métodos validan que los minutos sean `> 0` y lanzan `ValueError` si no.

### Estructuras de datos

- **`EstadoTipo`** — creencia sobre un tipo: `n`, `media`, varianza (vía Welford).
  Expone `varianza` y `desviacion` como propiedades.
- **`Prediccion`** — resultado de `predecir`, con los campos de arriba.

---

## 3. Compatibilidad con el backend (contrato US-C1)

El backend **no** usa la clase directamente: habla con el contrato de `schemas.py`,
donde **todos los minutos son enteros**. `estimador.py` trae dos funciones adaptadoras
que devuelven exactamente los objetos Pydantic del contrato, redondeando a `int`.

> Importante: Pydantic v2 **rechaza** un `float` en un campo `int` (`142.7` da
> `ValidationError`). Por eso el redondeo va en el adaptador, no es opcional.

### Cómo reemplazar los mocks de `main.py`

Hoy `main.py` tiene `/estimar` y `/estimador/actualizar` devolviendo datos fijos.
Para conectar el estimador real, basta esto:

```python
from estimador import Estimador, estimar_contrato, actualizar_contrato

estimador = Estimador()   # estado en memoria; persistir si se requiere

@app.post("/estimar", response_model=schemas.EstimarResponse)
def estimar(payload: schemas.EstimarRequest):
    return estimar_contrato(estimador, payload)

@app.post("/estimador/actualizar", response_model=schemas.ActualizarEstimadorResponse)
def actualizar_estimador(payload: schemas.ActualizarEstimadorRequest):
    return actualizar_contrato(estimador, payload)
```

El resto del loop del agente (`/tareas/{id}/terminar`) sigue igual: ya lee
`media_min`, `incertidumbre_min` y `n_observaciones` de la respuesta, que es justo
lo que devuelve el adaptador.

### Mapa de nombres (interno → contrato)

| Interno (`Estimador`) | Contrato (`schemas.py`) |
|---|---|
| `Prediccion.duracion_esperada_min` (float) | `EstimarResponse.duracion_esperada_min` (int) |
| `Prediccion.incertidumbre_min` (float) | `EstimarResponse.incertidumbre_min` (int) |
| `EstadoTipo.media` (float) | `ActualizarEstimadorResponse.media_min` (int) |
| `EstadoTipo.desviacion` (float) | `ActualizarEstimadorResponse.incertidumbre_min` (int) |
| `EstadoTipo.n` | `*.n_observaciones` |

---

## 4. Cómo correr la evidencia

```bash
python prueba_estimador.py
```

Simula un usuario que subestima "programación" (dice 90 min, real ~160) y mete 20
observaciones. Muestra en una tabla cómo la predicción migra de 90 hacia la media
real, y al final compara el error del estimador contra la **línea base** (usar la
estimación cruda del usuario). Tiene `assert`s que fallan si el estimador deja de
aprender, así que sirve de prueba automática además de demo.

---

## 5. Preguntas del jurado

**¿Por qué bayesiano y no un promedio simple?**
Un promedio da un número; esto da un número **y su incertidumbre**. La incertidumbre
alimenta el indicador de confianza y permite al planificador reservar más holgura en
tipos que conoce poco. Arrancamos con el prior del usuario y lo corregimos con
evidencia, ponderando por cantidad de datos, en vez de confiar ciegamente en pocos
registros.

**¿Por qué no una regresión sobre features de la tarea?**
Con los datos de tres personas en dos semanas y media no hay con qué ajustar una
regresión sin sobreajustar. El modelo por tipo es honesto con la cantidad de datos
que tenemos.

---

## 6. Pendientes (sprints 2 y 3)

| Historia | Qué falta | Sprint |
|---|---|---|
| US-A4 | generador de usuarios sintéticos (3 perfiles con sesgos) | 2 |
| US-A5 | evaluación contra línea base: error absoluto medio y % a tiempo | 3 |
| US-A6 | split entrenamiento/prueba | 3 |
| US-A7 | exponer el nivel de confianza por la API (el cálculo ya existe) | 3 |
| US-A8 | gráficas comparativas estimador vs línea base (si alcanza) | 3 |

Decisión abierta heredada del backend: quién suma el **margen de incertidumbre** a la
duración antes de enviarla al planificador (estimador, planificador o agente). Hoy el
agente pasa `duracion_esperada_min` sin margen.
