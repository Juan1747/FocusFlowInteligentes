# Planificador e interfaz — módulo de Juan David Cárdenas

Documentación del módulo **Planificador** de FocusFlow (historias US-B1 a US-B7) y de
la **interfaz** (las cinco pantallas). Está pensada para que cualquiera del equipo
pueda entenderlo, defenderlo ante el jurado y conectarlo sin leer todo el código.

| Archivo | Qué contiene | Historias |
|---|---|---|
| `planificador.py` | Modelo CSP con OR-Tools CP-SAT + adaptador al contrato | US-B1, US-B2, US-B3, US-B4 |
| `planificador_lineas_base.py` | EDF (línea base), recocido simulado y cota inferior | Evidencia de US-B1/B2 |
| `prueba_planificador.py` | 11 pruebas: restricciones, prioridad, determinismo, contrato, tiempo | US-B1 a US-B4 |
| `evaluacion_planificador.py` | Comparación contra las líneas base en 200 escenarios | Evidencia |
| `docs/evaluacion_planificador.md` | Resultados generados por el script anterior | Evidencia |
| `frontend/` | Las cinco pantallas en React + Vite | US-B5, US-B6 |

Dependencias: `pip install ortools` (el resto ya lo usa el backend). Para el frontend,
Node 20 o superior.

---

## 1. Qué hace y por qué

Recibe las tareas pendientes (con la duración que **predijo el estimador**, no la del
usuario), los bloques de tiempo libre y el descanso, y devuelve a qué hora va cada
tarea, cuáles están en riesgo y si todo cupo.

Armar la agenda **no es ordenar por fecha**. Es satisfacer restricciones a la vez: que
las tareas no se solapen, que cada una quepa entera en un bloque, que haya descanso
entre tareas, y que se equilibre prioridad contra plazo cuando no alcanza el tiempo.
Por eso se formula como un **problema de satisfacción de restricciones con objetivo**
y se resuelve con un solver.

---

## 2. Formulación del CSP (US-B1)

El tiempo se discretiza en **minutos enteros** desde un origen: el inicio del primer
bloque libre. Para cada tarea *i* y cada bloque *b* donde la tarea cabe entera:

### Variables y dominios

| Variable | Dominio | Significado |
|---|---|---|
| `inicio[i]` | unión de `[inicio_b, fin_b − d_i]` de los bloques donde cabe | minuto en que arranca la tarea |
| `x[i][b]` | {0, 1} | la tarea *i* va en el bloque *b* |
| `fuera[i]` | {0, 1} | la tarea *i* no cabe en esta agenda |
| `atraso[i]` | entero ≥ 0 | minutos que termina después de su plazo |

`d_i` es la duración (del estimador) y `w_i` = prioridad (**mayor = más importante**).

### Restricciones

1. **Un bloque o fuera:** `Σ_b x[i][b] + fuera[i] = 1`.
2. **Cabe entera:** si `x[i][b]`, entonces `inicio_b ≤ inicio[i]` y `inicio[i] + d_i ≤ fin_b`
   (en OR-Tools, `OnlyEnforceIf(x[i][b])`).
3. **Sin solapes y con descanso:** un `AddNoOverlap` sobre intervalos de largo
   `d_i + descanso`. Alargar el intervalo obliga al solver a dejar el descanso solo.
4. **Plazo blando:** `atraso[i] ≥ inicio[i] + d_i − limite[i]`. Si la tarea queda fuera,
   su atraso es el de hacerla justo después del último bloque libre.
5. **Redundante (solo acelera):** en cada bloque, `Σ_i (d_i + descanso)·x[i][b] ≤ largo_b + descanso`.

### Objetivo lexicográfico

Cada nivel solo se mejora sin empeorar el anterior (se resuelve por fases y se fija el
valor de cada una antes de pasar a la siguiente):

| Nivel | Qué minimiza | Por qué |
|---|---|---|
| 1 | `Σ w_i · fuera[i]` | agendar lo más posible, empezando por lo importante |
| 2 | `Σ w_i · atraso[i]` | atraso **ponderado** por prioridad (atrasar 10 min algo de prioridad baja no es lo mismo que atrasar una entrega) |
| 3 | `− min_i (limite_i − fin_i)` | **robustez**: dejar el mayor margen posible a la tarea más justa, para absorber un atraso |
| 4 | `Σ r_i · fuera[i]` | si hay que sacar una entre varias de igual prioridad, sale la de plazo más lejano |
| 5 | `Σ c_i · inicio[i]` | compactar: lo antes posible, con `c_i` mayor para plazos más cercanos |

Los niveles 4 y 5 hacen que la agenda sea **única y estable**: sin ellos, dos agendas de
igual costo se alternarían entre llamadas y la explicación del agente (`explicacion.py`)
anunciaría que "movió" tareas sin ninguna causa real.

### Tres decisiones de modelado que hay que saber defender

- **Plazo blando, no duro.** Con plazos duros, ante un día apretado el solver solo diría
  "infactible" y el usuario no sabría nada útil. Blando, entrega la mejor agenda posible
  y marca qué quedó en riesgo y por cuánto (US-B4).
- **`fuera[i]` en vez de exigir que todo quepa.** Con `Σ_b x[i][b] = 1` estricto, una sola
  tarea demasiado larga vuelve infactible toda la agenda.
- **Las tareas que quedan fuera cuentan atraso.** Una primera versión solo contaba el
  atraso de las agendadas, y la evaluación mostró que eso **premiaba sacar justo las
  tareas que irían tarde** (escondía su atraso). Se corrigió: una tarea que no cabe
  cuenta como hecha después del último bloque libre. Es un buen ejemplo de por qué se
  evalúa contra una línea base y no a ojo.

---

## 3. Cómo se resuelve

- **CP-SAT** combina búsqueda con backtracking, propagación de restricciones y
  aprendizaje de cláusulas (SAT), con varios hilos en paralelo. Es la técnica de búsqueda
  de la **semana 3** aplicada a un espacio de asignaciones; la herramienta la elegimos
  nosotros porque el curso no tiene notebook de CSP.
- **Punto de partida (warm start):** se le da al solver la mejor agenda entre EDF y un
  recocido simulado corto. Así nunca entrega algo peor que las líneas base y llega antes
  al óptimo.
- **Tiempo:** como mucho 5 s por planificación (1,5 + 1,5 + 0,75 + 0,5 + 0,75 por fase).
  Con 10–15 tareas suele terminar en menos de 1 s con optimalidad demostrada.
- **Estado:** `resolver()` reporta `OPTIMAL` si todas las fases demostraron optimalidad,
  `FEASIBLE` si alguna se cortó por tiempo.

---

## 4. API del módulo y contrato

```python
from planificador import planificar, construir_instancia, resolver

# Con la forma exacta del contrato (POST /planificar):
salida = planificar({
    "tareas": [{"id": 1, "tipo": "taller", "duracion_min": 90,
                "limite": "2026-11-23T23:59", "prioridad": 3}],
    "bloques_libres": [{"inicio": "2026-11-23T18:00", "fin": "2026-11-23T21:00"}],
    "descanso_min": 15,
})
# {"factible": True, "agenda": [...], "en_riesgo": [...], "explicacion": ""}

# Con información extra (estado del solver, tiempo, atraso, no agendadas):
instancia = construir_instancia(tareas, bloques, 15)
resultado = resolver(instancia)
```

`python planificador.py` corre el ejemplo base de 3 tareas en 2 bloques.

### Cómo quedó conectado al backend

`main.py` reemplazó el mock de `/planificar` por el adaptador, igual que el estimador:

```python
from planificador import planificar_contrato

@app.post("/planificar", response_model=schemas.PlanificarResponse)
def planificar(payload: schemas.PlanificarRequest):
    return planificar_contrato(payload)
```

El loop del agente no cambia: ya llamaba a `planificar()` para la agenda vieja y la nueva.

### Semántica de la salida (para cerrar con el equipo)

| Campo | Regla |
|---|---|
| `agenda` | tareas que cupieron, ordenadas por inicio; `fin = inicio + duración`; ISO con segundos |
| `en_riesgo` | tareas con holgura (`limite − fin`) **menor que 30 min**, incluidas las atrasadas (holgura negativa), ordenadas de la más grave a la menos grave |
| tareas que no caben | no aparecen en `agenda` y **siempre** van en `en_riesgo`, con la holgura que tendrían si se hicieran después del último bloque libre |
| `factible` | `true` si todas las tareas cupieron en algún bloque, aunque alguna vaya tarde |
| `explicacion` | siempre `""`; la llena el agente |

Constantes en `planificador.py`: `UMBRAL_RIESGO_MIN = 30`, `DESCANSO_POR_DEFECTO_MIN = 15`,
`TIEMPO_POR_FASE_S`. Si se cambia el umbral, cambiarlo también en `frontend/src/constantes.js`.

---

## 5. Evidencia contra la línea base

`python evaluacion_planificador.py --guardar` compara, sobre **los mismos escenarios**:

| Método | Qué es | Semana |
|---|---|---|
| **EDF** (línea base) | ordenar por fecha límite y meter cada tarea en el primer hueco | 3, búsqueda voraz |
| **Recocido simulado** (alternativa) | búsqueda local sobre el orden de las tareas | 3, búsqueda local |
| **CP-SAT** (FocusFlow) | el modelo de arriba | 3, CSP / búsqueda |
| **Cota inferior** | relajación: cada tarea con el tiempo libre para ella sola | 3, relajación |

**Partición honesta:** los escenarios 0–49 se usaron para ajustar (tiempo por fase,
iteraciones del recocido, umbral). Los resultados se reportan **solo** sobre los
escenarios 50–199, que no se tocaron. Los escenarios imitan la semana de un estudiante:
bloques por la tarde y la noche, tareas de 30 min a 4 h, plazos en la semana, y una
presión (minutos de tareas / minutos libres) entre 0,6 y 1,3, así que muchos días
**no alcanzan** a propósito.

Resultados (150 escenarios de prueba, ver `docs/evaluacion_planificador.md`):

- **CP-SAT frente a EDF:** mejor en 124, igual en 26, **peor en 0**. Donde ambos dejan
  fuera la misma prioridad, CP-SAT tiene **19 % menos atraso efectivo**; además deja
  fuera casi la mitad de prioridad (2,22 contra 4,16 de media) y agenda 84,7 % de las
  tareas contra 79,4 %.
- **CP-SAT frente al recocido:** mejor en 19, igual en 131, peor en 0; la diferencia en
  atraso es pequeña (2 %).
- **Optimalidad demostrada en 141 de 150 escenarios (94 %)** dentro de 5 s.

Cómo leerlo, sin inflarlo:

- Contra la línea base la ventaja es clara y consistente.
- El recocido simulado queda cerca. Lo descartamos como planificador **no por calidad
  promedio sino por garantías**: no sabe si su agenda es óptima ni cuándo parar, su
  resultado depende de la semilla, y cada restricción nueva hay que meterla a mano en el
  decodificador. CP-SAT declara restricciones y objetivo, y **demuestra** optimalidad en
  el 94 % de los casos. Igual lo aprovechamos: es el punto de partida del solver.
- La cota inferior está lejos porque ignora que las tareas compiten por el mismo tiempo;
  con presión > 1 ningún método puede acercarse. Sirve como piso, no como meta.

---

## 6. Interfaz (US-B5, US-B6)

Cinco pantallas en React + Vite (`frontend/`):

| Pantalla | Qué hace | Endpoints |
|---|---|---|
| **Hoy** | agenda del día, cifras, tareas en curso; *Empezar* y *Terminar* con horas reales editables | `GET /tareas`, `PATCH /tareas/{id}`, `POST /tareas/{id}/terminar` |
| **Nueva tarea** | nombre, tipo, límite, prioridad y estimación; muestra en vivo lo que reservará FocusFlow y su confianza | `POST /estimar`, `POST /tareas` |
| **Bloques libres** | tiempo libre por día, con plantillas; valida rangos y solapes como el backend | `GET` y `POST /bloques-libres` |
| **Agenda propuesta** | agenda por día, riesgo con sus minutos, tareas sin espacio | `POST /estimar`, `POST /planificar` |
| **Replanificación** | explicación del agente y tabla antes/después de cada tarea | respuesta de `/terminar` |

La confianza (US-A7) y el riesgo (US-B4) se ven en cada tarea, **siempre con texto**, no
solo con color.

### Cómo correrla

```bash
# Terminal 1 (raíz del repo)
uvicorn main:app --reload

# Terminal 2
cd frontend
npm install        # solo la primera vez
npm run dev        # http://localhost:5173
```

El servidor de Vite reenvía `/api/*` al backend (ver `vite.config.js`), así que el
backend **no necesita CORS**. Otro backend: `FOCUSFLOW_API=http://host:puerto npm run dev`.

### Lo que la interfaz hace mientras no exista `GET /agenda`

El backend solo devuelve la agenda al terminar una tarea. Para mostrarla en Hoy y en
Agenda, `frontend/src/api.js` (`calcularAgenda`) repite **los pasos 3 y 4 del agente**:
re-estima las tareas no terminadas con `/estimar`, recorta los bloques libres desde
ahora y llama a `/planificar` con 15 min de descanso. Cuando Chacón exponga `GET /agenda`,
esa función se reduce a una llamada.

---

## 7. Decisiones tomadas que el equipo debe confirmar

| Decisión | Lo que quedó | Dónde cambiarlo |
|---|---|---|
| Dirección de `prioridad` | mayor = más importante; w_i = prioridad | `planificador.py`, `formato.js` |
| Umbral de riesgo | 30 min | `UMBRAL_RIESGO_MIN` + `constantes.js` |
| `factible` | todas cupieron, aunque alguna vaya tarde | `construir_resultado` |
| Holgura de las que no caben | la que tendrían hechas después del último bloque | `construir_resultado` |
| Tipos de tarea | `programacion`, `lectura`, `taller`, `estudio_parcial` | `formato.js` (`TIPOS`) |
| Margen de incertidumbre (US-D4) | **no se aplica todavía**: el planificador usa la duración que recibe. Propuesta: que lo sume el agente (opción C), sin cambiar el contrato | pendiente del cross-review del 27 nov |

---

## 8. Preguntas probables del jurado

| Pregunta | Respuesta corta |
|---|---|
| ¿Por qué no ordenar por fecha límite? | Es nuestra línea base. No equilibra prioridad contra plazo ni aprovecha huecos; perdió o empató en los 150 escenarios de prueba y nunca ganó. |
| ¿Por qué no A*? | El espacio de agendas parciales crece factorialmente y no hay una heurística admisible clara. CP-SAT declara restricciones y objetivo y demuestra optimalidad. |
| ¿Por qué no recocido simulado, si quedó tan cerca? | Por garantías, no por promedio (sección 5). Además lo usamos como punto de partida del solver. |
| ¿Por qué no un LLM? | Un LLM no garantiza que no haya solapes ni que se respeten los bloques, y puede inventar horas. La semana 10 habla justamente de qué no delegar a un modelo. |
| ¿El curso vio CSP? | La técnica es búsqueda en el espacio de asignaciones (semana 3). La herramienta la elegimos nosotros y lo decimos. |
| ¿Es óptima la agenda? | Cuando el estado es `OPTIMAL`, sí, para este modelo: pasó en el 94 % de los escenarios. |
| ¿Qué pasa si no cabe todo? | El plazo es blando: se entrega la mejor agenda y se marca qué quedó en riesgo y por cuánto. |
| ¿Cómo conecta con el estimador? | Recibe la duración predicha, no la del usuario. Cuando el estimador aprende que una tarea tarda más, la agenda cambia. |

---

## 9. Límites (para US-D3)

| Límite | Mitigación |
|---|---|
| Una tarea no se parte entre bloques | el usuario la divide en dos tareas |
| No modela la energía por hora (rendir menos de noche) | queda como trabajo futuro; uno de los perfiles sintéticos de Alvaro lo muestra |
| Con muchas tareas y poco tiempo, el límite de 5 s puede cortar la búsqueda (`FEASIBLE`) y, en ese caso, dos llamadas iguales pueden dar agendas distintas | con 10–15 tareas se demuestra el óptimo y la agenda es determinista; se reporta el estado |
| La agenda es tan buena como la estimación de duración | el margen de incertidumbre (US-D4) |
| Replanificar a cada rato puede molestar | US-B7 (límite diario) si alcanza el tiempo |
| La evaluación usa escenarios sintéticos | se declara; con los datos reales de US-D2 se repite al cierre |
