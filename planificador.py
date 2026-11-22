"""
FocusFlow — Planificador con restricciones (módulo de Juan David Cárdenas).

Historias que implementa este archivo:
    US-B1  Ubicar todas las tareas dentro de los bloques libres sin solaparlas,
           o reportar que no caben.
    US-B2  Respetar plazos (blandos) y un descanso mínimo configurable.
    US-B3  Replanificar en pocos segundos: el agente lo llama cada vez que una
           tarea termina (ver `terminar_tarea` en main.py).
    US-B4  Marcar las tareas en riesgo de no cumplir su plazo.

Idea central
------------
Armar la agenda NO es ordenar por fecha: es un problema de satisfacción de
restricciones (CSP) con una función objetivo. Lo resolvemos con OR-Tools CP-SAT,
que hace búsqueda con backtracking, propagación de restricciones y aprendizaje
de cláusulas (semana 3 del curso: búsqueda en el espacio de asignaciones).

El tiempo se discretiza en minutos enteros desde un origen (el inicio del primer
bloque libre). Para cada tarea i y cada bloque b donde la tarea cabe:

    Variables
        inicio[i]   entero: minuto en que arranca la tarea i.
        x[i][b]     booleano: la tarea i va en el bloque b.
        fuera[i]    booleano: la tarea i no cabe en esta agenda.
        atraso[i]   entero >= 0: minutos que la tarea termina después de su plazo.

    Restricciones
        (1) sum_b x[i][b] + fuera[i] == 1          un bloque, o queda fuera
        (2) x[i][b]  =>  inicio_b <= inicio[i]  y  inicio[i] + d[i] <= fin_b
        (3) NoOverlap de los intervalos [inicio[i], inicio[i] + d[i] + descanso)
            -> no hay solapes y entre dos tareas siempre queda el descanso
        (4) atraso[i] >= inicio[i] + d[i] - limite[i]     (plazo BLANDO)

    Objetivo (lexicográfico: cada nivel solo se mejora sin empeorar el anterior)
        1. min sum_i w[i] * fuera[i]       agendar lo más posible, por prioridad
        2. min sum_i w[i] * atraso[i]      minimizar atraso ponderado; una tarea
                                           que queda fuera cuenta como hecha justo
                                           después del último bloque libre (si no,
                                           sacar tareas "escondería" su atraso)
        3. max min_i (limite[i] - fin[i])  robustez: dejar el mayor margen posible
                                           a la tarea más justa, para absorber
                                           un atraso sin romper su plazo
        4. min sum_i r[i] * fuera[i]       si hay que dejar fuera una entre varias
                                           de igual prioridad, la de plazo más lejano
        5. min sum_i c[i] * inicio[i]      compactar: lo antes posible, con c[i]
                                           mayor para plazos más cercanos
        (4 y 5 hacen la agenda estable y determinista)

    con w[i] = prioridad (mayor = más importante).

Por qué el plazo es blando: si fuera duro, ante un día apretado el solver solo
respondería "infactible" y el usuario no sabría nada útil. Blando, entrega la mejor
agenda posible y dice qué quedó en riesgo y por cuánto (US-B4).

Por qué el desempate importa: sin él, dos agendas de igual costo pueden alternarse
entre llamadas, y la explicación del agente (explicacion.py) anunciaría que "movió"
tareas sin ninguna causa real.

Este módulo no conoce al estimador ni a FastAPI: recibe y devuelve diccionarios
con la forma exacta del contrato (US-C1). `planificar_contrato` es el adaptador
que usa main.py, igual que `estimar_contrato` en estimador.py.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from ortools.sat.python import cp_model

# ---------- Parámetros del módulo ----------

# Descanso entre tareas cuando la petición no lo trae (regla del contrato).
DESCANSO_POR_DEFECTO_MIN = 15

# US-B4: una tarea está "en riesgo" si su holgura (límite - fin) es menor que esto.
# Incluye las atrasadas (holgura negativa) y las que quedan justas.
UMBRAL_RIESGO_MIN = 30

# US-B3: tiempo máximo de cómputo por fase del objetivo lexicográfico.
# Cinco fases -> como mucho 5 s por replanificación (casi siempre < 1 s).
TIEMPO_POR_FASE_S = (1.5, 1.5, 0.75, 0.5, 0.75)

# Iteraciones de la búsqueda local que da el punto de partida al solver.
ITERACIONES_ARRANQUE = 1500

# Reproducibilidad: misma entrada -> misma agenda.
SEMILLA = 0
NUM_WORKERS = 8

FechaHora = Union[str, datetime]


# ---------- Datos de entrada ya discretizados ----------

@dataclass(frozen=True)
class TareaMin:
    """Una tarea en minutos desde el origen."""

    id: int
    tipo: str
    duracion: int        # minutos, > 0
    limite: int          # minuto del plazo (puede ser negativo si ya venció)
    peso: int            # w[i] = prioridad
    limite_dt: datetime  # el plazo original, para reportar


@dataclass(frozen=True)
class BloqueMin:
    """Un bloque libre en minutos desde el origen: [inicio, fin)."""

    inicio: int
    fin: int

    @property
    def largo(self) -> int:
        return self.fin - self.inicio


@dataclass
class Instancia:
    """Un problema de planificación listo para resolver.

    Todos los algoritmos (CP-SAT y las líneas base) trabajan sobre la misma
    instancia, así que se comparan sobre exactamente el mismo problema.
    """

    origen: datetime
    tareas: List[TareaMin]
    bloques: List[BloqueMin]
    descanso: int

    def a_datetime(self, minuto: int) -> datetime:
        return self.origen + timedelta(minutes=minuto)

    def bloques_validos(self, tarea: TareaMin) -> List[BloqueMin]:
        """Bloques donde la tarea cabe entera (una tarea no se parte)."""
        return [b for b in self.bloques if b.largo >= tarea.duracion]

    @property
    def horizonte(self) -> int:
        """Fin del último bloque libre (minutos desde el origen)."""
        return max((b.fin for b in self.bloques), default=0)

    def atraso_si_queda_fuera(self, tarea: TareaMin) -> int:
        """Atraso de una tarea que no cabe: se asume que se hace justo después
        del último bloque libre declarado. Contarlo evita que el solver "esconda"
        atraso dejando fuera justo las tareas que irían tarde."""
        return max(0, self.horizonte + tarea.duracion - tarea.limite)


def atraso_efectivo(instancia: Instancia, inicios: Mapping[int, Optional[int]]) -> int:
    """Atraso ponderado de una agenda, contando también las tareas que quedaron
    fuera. Es el nivel 2 del objetivo, y la misma medida para todos los métodos."""
    total = 0
    for t in instancia.tareas:
        inicio = inicios.get(t.id)
        if inicio is None:
            total += t.peso * instancia.atraso_si_queda_fuera(t)
        else:
            total += t.peso * max(0, inicio + t.duracion - t.limite)
    return total


@dataclass
class Resultado:
    """Salida de cualquier método de planificación.

    `agenda` y `en_riesgo` tienen la forma del contrato; el resto es información
    extra para la evaluación y el log (no viaja por la API).
    """

    factible: bool
    agenda: List[Dict[str, Any]]
    en_riesgo: List[Dict[str, int]]
    no_agendadas: List[int] = field(default_factory=list)
    inicios: Dict[int, Optional[int]] = field(default_factory=dict)
    atraso_ponderado: int = 0   # atraso efectivo (incluye las que quedaron fuera)
    estado: str = ""            # OPTIMAL / FEASIBLE / EDF_RESPALDO / ...
    segundos: float = 0.0

    def a_contrato(self) -> Dict[str, Any]:
        return {
            "factible": self.factible,
            "agenda": self.agenda,
            "en_riesgo": self.en_riesgo,
            "explicacion": "",
        }


# ---------- Conversión desde el contrato ----------

def _a_datetime(valor: FechaHora) -> datetime:
    if isinstance(valor, datetime):
        return valor
    return datetime.fromisoformat(str(valor))


def _minutos_desde(origen: datetime, dt: datetime) -> float:
    return (dt - origen).total_seconds() / 60.0


def _campo(obj: Any, nombre: str, defecto: Any = None) -> Any:
    """Lee un campo de un dict o de un objeto (Pydantic, dataclass)."""
    if isinstance(obj, Mapping):
        return obj.get(nombre, defecto)
    return getattr(obj, nombre, defecto)


def construir_instancia(
    tareas: Sequence[Any],
    bloques_libres: Sequence[Any],
    descanso_min: Optional[int] = None,
) -> Instancia:
    """Pasa la entrada del contrato (fechas ISO) a minutos enteros.

    - El origen es el inicio del primer bloque, redondeado al minuto.
    - El inicio de cada bloque se redondea hacia arriba y el fin hacia abajo,
      para que una tarea agendada nunca se salga de un bloque declarado.
    - Una duración con decimales se redondea hacia arriba: reservar de más es
      más seguro que reservar de menos.
    """
    descanso = DESCANSO_POR_DEFECTO_MIN if descanso_min is None else int(descanso_min)
    if descanso < 0:
        raise ValueError("descanso_min no puede ser negativo")

    bloques_dt = []
    for b in bloques_libres:
        inicio, fin = _a_datetime(_campo(b, "inicio")), _a_datetime(_campo(b, "fin"))
        if fin <= inicio:
            raise ValueError("en cada bloque, fin debe ser posterior a inicio")
        bloques_dt.append((inicio, fin))
    bloques_dt.sort()

    if bloques_dt:
        origen = bloques_dt[0][0].replace(second=0, microsecond=0)
    else:
        # Sin bloques no se puede agendar nada; el origen solo sirve para reportar.
        limites = [_a_datetime(_campo(t, "limite")) for t in tareas]
        origen = min(limites).replace(second=0, microsecond=0) if limites else datetime(2000, 1, 1)

    bloques = []
    for inicio, fin in bloques_dt:
        a = math.ceil(_minutos_desde(origen, inicio))
        z = math.floor(_minutos_desde(origen, fin))
        if z > a:
            bloques.append(BloqueMin(a, z))

    tareas_min = []
    vistos = set()
    for t in tareas:
        tid = int(_campo(t, "id"))
        if tid in vistos:
            raise ValueError(f"id de tarea repetido: {tid}")
        vistos.add(tid)
        duracion = math.ceil(float(_campo(t, "duracion_min")))
        if duracion <= 0:
            raise ValueError("duracion_min debe ser mayor que 0")
        prioridad = int(_campo(t, "prioridad"))
        if prioridad < 1:
            raise ValueError("prioridad debe ser al menos 1")
        limite_dt = _a_datetime(_campo(t, "limite"))
        tareas_min.append(
            TareaMin(
                id=tid,
                tipo=str(_campo(t, "tipo", "")),
                duracion=duracion,
                limite=math.floor(_minutos_desde(origen, limite_dt)),
                peso=prioridad,
                limite_dt=limite_dt,
            )
        )

    return Instancia(origen=origen, tareas=tareas_min, bloques=bloques, descanso=descanso)


# ---------- Resultado común a todos los métodos ----------

def orden_por_plazo(instancia: Instancia) -> List[TareaMin]:
    """Plazo más cercano primero; a igual plazo, mayor prioridad; luego id.
    Es el orden de la línea base EDF y el que usa el desempate."""
    return sorted(instancia.tareas, key=lambda t: (t.limite, -t.peso, t.id))


def construir_resultado(
    instancia: Instancia,
    inicios: Mapping[int, Optional[int]],
    estado: str,
    segundos: float = 0.0,
) -> Resultado:
    """Arma la salida del contrato a partir de los minutos de inicio.

    `inicios[id]` es el minuto de arranque, o None si la tarea quedó fuera.

    holgura_min = límite - fin. Una tarea que no cupo va SIEMPRE en riesgo (no
    tiene tiempo reservado), con la holgura que tendría si se hiciera justo
    después del último bloque libre: negativa = ni así llega a su plazo.
    """
    agenda, en_riesgo, no_agendadas = [], [], []
    for t in instancia.tareas:
        inicio = inicios.get(t.id)
        if inicio is None:
            no_agendadas.append(t.id)
            en_riesgo.append(
                {"tarea_id": t.id, "holgura_min": t.limite - (instancia.horizonte + t.duracion)}
            )
            continue
        fin = inicio + t.duracion
        agenda.append(
            {
                "tarea_id": t.id,
                "inicio": instancia.a_datetime(inicio).isoformat(timespec="seconds"),
                "fin": instancia.a_datetime(fin).isoformat(timespec="seconds"),
            }
        )
        holgura = t.limite - fin
        if holgura < UMBRAL_RIESGO_MIN:
            en_riesgo.append({"tarea_id": t.id, "holgura_min": int(holgura)})

    agenda.sort(key=lambda a: (a["inicio"], a["tarea_id"]))
    en_riesgo.sort(key=lambda r: (r["holgura_min"], r["tarea_id"]))
    return Resultado(
        factible=not no_agendadas,
        agenda=agenda,
        en_riesgo=en_riesgo,
        no_agendadas=sorted(no_agendadas),
        inicios=dict(inicios),
        atraso_ponderado=atraso_efectivo(instancia, inicios),
        estado=estado,
        segundos=segundos,
    )


# ---------- El modelo CP-SAT (US-B1, US-B2) ----------

def resolver(
    instancia: Instancia,
    tiempo_por_fase_s: Sequence[float] = TIEMPO_POR_FASE_S,
) -> Resultado:
    """Resuelve la instancia con CP-SAT y devuelve la mejor agenda encontrada."""
    reloj = time.perf_counter()
    if not instancia.tareas:
        return construir_resultado(instancia, {}, "OPTIMAL", 0.0)

    modelo = cp_model.CpModel()
    inicio: Dict[int, cp_model.IntVar] = {}
    fuera: Dict[int, Any] = {}
    atraso: Dict[int, cp_model.IntVar] = {}
    intervalos = []
    sin_bloque: List[int] = []  # tareas más largas que cualquier bloque
    # Para la restricción redundante de capacidad: (x, tiempo ocupado) por bloque.
    carga: Dict[int, List[Tuple[Any, int]]] = {k: [] for k in range(len(instancia.bloques))}

    for t in instancia.tareas:
        validos = [
            (k, b) for k, b in enumerate(instancia.bloques) if b.largo >= t.duracion
        ]
        if not validos:
            sin_bloque.append(t.id)
            continue

        # Dominio de inicio[i]: la unión de los rangos donde la tarea cabe.
        dominio = cp_model.Domain.FromIntervals(
            [[b.inicio, b.fin - t.duracion] for _, b in validos]
        )
        inicio[t.id] = modelo.NewIntVarFromDomain(dominio, f"inicio_{t.id}")
        fuera[t.id] = modelo.NewBoolVar(f"fuera_{t.id}")

        en_bloque = []
        for k, b in validos:
            x = modelo.NewBoolVar(f"x_{t.id}_{k}")
            # (2) si va en el bloque b, cabe entera dentro de b.
            modelo.Add(inicio[t.id] >= b.inicio).OnlyEnforceIf(x)
            modelo.Add(inicio[t.id] + t.duracion <= b.fin).OnlyEnforceIf(x)
            # (3) intervalo alargado con el descanso, presente solo si x.
            intervalos.append(
                modelo.NewOptionalFixedSizeIntervalVar(
                    inicio[t.id], t.duracion + instancia.descanso, x, f"iv_{t.id}_{k}"
                )
            )
            en_bloque.append(x)
            carga[k].append((x, t.duracion + instancia.descanso))

        # (1) exactamente un bloque, o fuera.
        modelo.AddExactlyOne(en_bloque + [fuera[t.id]])
        # Si queda fuera, su inicio no significa nada: lo fijamos para que la
        # solución sea única.
        modelo.Add(inicio[t.id] == dominio.min()).OnlyEnforceIf(fuera[t.id])

        # (4) plazo blando.
        # Si queda fuera, su atraso es el de hacerla después del último bloque.
        atraso_fuera = instancia.atraso_si_queda_fuera(t)
        cota = max(atraso_fuera, max(b.fin for _, b in validos) - t.limite, 0)
        atraso[t.id] = modelo.NewIntVar(0, cota, f"atraso_{t.id}")
        modelo.Add(atraso[t.id] >= inicio[t.id] + t.duracion - t.limite).OnlyEnforceIf(
            fuera[t.id].Not()
        )
        modelo.Add(atraso[t.id] == atraso_fuera).OnlyEnforceIf(fuera[t.id])

    if not inicio:
        # Ninguna tarea cabe en ningún bloque: no hay nada que optimizar.
        return construir_resultado(
            instancia, {tid: None for tid in sin_bloque}, "OPTIMAL",
            time.perf_counter() - reloj,
        )

    modelo.AddNoOverlap(intervalos)

    # Restricción redundante (no cambia las soluciones, solo acelera la búsqueda):
    # lo que se mete en un bloque, con sus descansos, no supera su largo. El
    # descanso de la última tarea puede quedar fuera del bloque, por eso + descanso.
    for k, b in enumerate(instancia.bloques):
        if len(carga[k]) > 1:
            modelo.Add(sum(ocupa * x for x, ocupa in carga[k]) <= b.largo + instancia.descanso)

    por_id = {t.id: t for t in instancia.tareas}
    ids = list(inicio)

    # Holgura mínima entre las tareas agendadas: límite - fin de la más justa.
    holguras_posibles = [
        por_id[i].limite - (b.fin) for i in ids for b in instancia.bloques_validos(por_id[i])
    ] + [
        por_id[i].limite - (b.inicio + por_id[i].duracion)
        for i in ids for b in instancia.bloques_validos(por_id[i])
    ]
    holgura_min = modelo.NewIntVar(min(holguras_posibles), max(holguras_posibles), "holgura_min")
    for i in ids:
        t = por_id[i]
        modelo.Add(holgura_min <= t.limite - inicio[i] - t.duracion).OnlyEnforceIf(
            fuera[i].Not()
        )

    # c[i] decrece con el orden por plazo: a igualdad de todo lo demás, la de
    # plazo más cercano tiene más incentivo para ir antes.
    orden = [t.id for t in orden_por_plazo(instancia) if t.id in inicio]
    coef = {tid: 2 * len(orden) - k for k, tid in enumerate(orden)}

    # Si, con todo lo anterior empatado, hay que elegir cuál de varias tareas de
    # igual prioridad queda fuera, queda fuera la de plazo más lejano (la menos
    # urgente). Sin esto la elección cambiaría entre llamadas.
    rango_fuera = {tid: len(orden) - k for k, tid in enumerate(orden)}

    objetivos = [
        sum(por_id[i].peso * fuera[i] for i in ids),   # 1. agendar lo más posible
        sum(por_id[i].peso * atraso[i] for i in ids),  # 2. atraso ponderado
        -holgura_min,                                   # 3. robustez ante atrasos
        sum(rango_fuera[i] * fuera[i] for i in ids),   # 4. desempate: cuál queda fuera
        sum(coef[i] * inicio[i] for i in ids),         # 5. compactar y desempatar
    ]

    # Punto de partida (warm start): la mejor agenda entre EDF y una búsqueda
    # local corta. El solver arranca de una solución buena y su trabajo es
    # mejorarla y demostrar si es óptima; nunca entrega algo peor que ella.
    from planificador_lineas_base import costo, decodificar, recocido_simulado

    candidatas = [
        decodificar(instancia, orden_por_plazo(instancia)),
        recocido_simulado(instancia, iteraciones=ITERACIONES_ARRANQUE).inicios,
    ]
    arranque = min(candidatas, key=lambda inicios: costo(instancia, inicios))
    for i, ini in arranque.items():
        if i in inicio:
            modelo.AddHint(fuera[i], ini is None)
            if ini is not None:
                modelo.AddHint(inicio[i], ini)

    solver = cp_model.CpSolver()
    solver.parameters.num_workers = NUM_WORKERS
    solver.parameters.random_seed = SEMILLA

    estados = []
    solucion: Optional[Dict[int, Optional[int]]] = None
    for objetivo, limite_s in zip(objetivos, tiempo_por_fase_s):
        modelo.Minimize(objetivo)
        solver.parameters.max_time_in_seconds = float(limite_s)
        estado = solver.Solve(modelo)
        if estado not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # Sin solución nueva en esta fase: nos quedamos con la anterior.
            estados.append(solver.StatusName(estado))
            break
        estados.append(solver.StatusName(estado))
        solucion = {
            i: (None if solver.BooleanValue(fuera[i]) else solver.Value(inicio[i]))
            for i in ids
        }
        # Fijar el nivel alcanzado antes de optimizar el siguiente, y arrancar
        # la próxima fase desde esta solución.
        modelo.Add(objetivo <= int(round(solver.ObjectiveValue())))
        modelo.ClearHints()
        for i in ids:
            modelo.AddHint(inicio[i], solver.Value(inicio[i]))
            modelo.AddHint(fuera[i], solver.BooleanValue(fuera[i]))

    segundos = time.perf_counter() - reloj

    if solucion is None:
        # Nunca debería pasar (dejar todo fuera siempre es factible), pero la
        # demo no puede caerse: usamos la línea base voraz como respaldo.
        from planificador_lineas_base import edf

        respaldo = edf(instancia)
        respaldo.estado = "EDF_RESPALDO"
        respaldo.segundos = segundos
        return respaldo

    for tid in sin_bloque:
        solucion[tid] = None
    # La agenda es óptima solo si todas las fases lo fueron.
    estado_final = "OPTIMAL" if estados == ["OPTIMAL"] * len(objetivos) else "FEASIBLE"
    return construir_resultado(instancia, solucion, estado_final, segundos)


# ---------- Interfaz del contrato (US-C1) ----------

def planificar(peticion: Mapping[str, Any]) -> Dict[str, Any]:
    """Entrada y salida con la forma exacta de POST /planificar.

    >>> planificar({"tareas": [...], "bloques_libres": [...], "descanso_min": 15})
    {"factible": True, "agenda": [...], "en_riesgo": [...], "explicacion": ""}
    """
    instancia = construir_instancia(
        peticion.get("tareas", []),
        peticion.get("bloques_libres", []),
        peticion.get("descanso_min"),
    )
    return resolver(instancia).a_contrato()


def planificar_contrato(payload: Any) -> Any:
    """Adaptador para main.py: recibe `schemas.PlanificarRequest` y devuelve
    `schemas.PlanificarResponse`. Importa schemas de forma perezosa para que el
    módulo se pueda usar y probar sin el backend."""
    import schemas

    instancia = construir_instancia(payload.tareas, payload.bloques_libres, payload.descanso_min)
    resultado = resolver(instancia)
    return schemas.PlanificarResponse(**resultado.a_contrato())


# ---------- Ejemplo mínimo (primera tarea concreta de US-B1) ----------

if __name__ == "__main__":
    import json
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ejemplo = {
        "tareas": [
            {"id": 1, "tipo": "taller", "duracion_min": 90,
             "limite": "2026-11-23T23:59", "prioridad": 3},
            {"id": 2, "tipo": "lectura", "duracion_min": 60,
             "limite": "2026-11-24T12:00", "prioridad": 2},
            {"id": 3, "tipo": "programacion", "duracion_min": 150,
             "limite": "2026-11-24T23:59", "prioridad": 3},
        ],
        "bloques_libres": [
            {"inicio": "2026-11-23T18:00", "fin": "2026-11-23T21:00"},
            {"inicio": "2026-11-24T08:00", "fin": "2026-11-24T11:00"},
        ],
        "descanso_min": 15,
    }
    instancia = construir_instancia(ejemplo["tareas"], ejemplo["bloques_libres"], 15)
    resultado = resolver(instancia)
    print(f"Estado del solver: {resultado.estado} ({resultado.segundos:.2f} s)")
    print(json.dumps(resultado.a_contrato(), indent=2, ensure_ascii=False))
