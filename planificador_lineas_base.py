"""
FocusFlow — Líneas base del planificador (evidencia de US-B1 y US-B2).

Para afirmar que el solver "planifica mejor" hay que compararlo contra algo.
Este archivo tiene los métodos contra los que se compara CP-SAT, todos sobre la
misma `Instancia` y con las mismas reglas (bloques, descanso, plazos blandos):

    edf               Línea base: ordenar por fecha límite (Earliest Deadline First)
                      y meter cada tarea en el primer hueco donde quepa.
                      Es búsqueda voraz (semana 3): decide una vez y no corrige.

    recocido_simulado Alternativa considerada: búsqueda local (semana 3) sobre el
                      orden de las tareas, usando el mismo decodificador de EDF.
                      No garantiza optimalidad ni sabe cuándo parar.

    cota_inferior     Relajación del problema (semana 3): se quita la restricción
                      de no solapamiento y cada tarea va sola a su mejor bloque.
                      Ningún método puede tener menos atraso que esto, así que
                      mide qué tan lejos del ideal queda cada uno.
"""

from __future__ import annotations

import math
import random
import time
from typing import Dict, List, Optional, Sequence, Tuple

from planificador import (
    Instancia,
    Resultado,
    TareaMin,
    atraso_efectivo,
    construir_resultado,
    orden_por_plazo,
)


# ---------- Decodificador común: orden de tareas -> agenda ----------

def decodificar(instancia: Instancia, orden: Sequence[TareaMin]) -> Dict[int, Optional[int]]:
    """Ubica las tareas en el orden dado, cada una en el hueco más temprano
    donde quepa entera con su descanso ("first fit").

    Usa la misma regla que el modelo CP-SAT: cada tarea ocupa
    [inicio, inicio + duración + descanso) y esos intervalos no se solapan.
    """
    ocupados: List[Tuple[int, int]] = []  # intervalos alargados, ordenados
    inicios: Dict[int, Optional[int]] = {}

    for t in orden:
        largo = t.duracion + instancia.descanso
        elegido = None
        for b in instancia.bloques:
            if b.largo < t.duracion:
                continue
            candidato = b.inicio
            for a, z in ocupados:
                if z <= candidato:
                    continue
                if a >= candidato + largo:
                    break
                candidato = z  # choca: probar justo después de esa tarea
            if candidato + t.duracion <= b.fin:
                elegido = candidato
                break
        inicios[t.id] = elegido
        if elegido is not None:
            ocupados.append((elegido, elegido + largo))
            ocupados.sort()
    return inicios


def costo(instancia: Instancia, inicios: Dict[int, Optional[int]]) -> Tuple[int, int]:
    """Los dos primeros niveles del objetivo de CP-SAT, en el mismo orden:
    (prioridad que queda fuera, atraso efectivo). Todos los métodos optimizan
    y se miden con esta misma función."""
    fuera = sum(t.peso for t in instancia.tareas if inicios.get(t.id) is None)
    return fuera, atraso_efectivo(instancia, inicios)


# ---------- Línea base: EDF ----------

def edf(instancia: Instancia) -> Resultado:
    """Earliest Deadline First con colocación voraz."""
    reloj = time.perf_counter()
    inicios = decodificar(instancia, orden_por_plazo(instancia))
    return construir_resultado(instancia, inicios, "EDF", time.perf_counter() - reloj)


# ---------- Alternativa: recocido simulado ----------

def recocido_simulado(
    instancia: Instancia,
    iteraciones: int = 3000,
    temperatura_inicial: float = 200.0,
    enfriamiento: float = 0.998,
    semilla: int = 0,
) -> Resultado:
    """Búsqueda local sobre el orden de las tareas.

    Arranca del orden EDF. En cada paso propone mover una tarea a otra posición
    o intercambiar dos; acepta peores soluciones con probabilidad
    exp(-delta / T), y T baja geométricamente (recocido simulado, semana 3).
    """
    reloj = time.perf_counter()
    rng = random.Random(semilla)
    orden = list(orden_por_plazo(instancia))
    n = len(orden)

    # Escalar el costo lexicográfico a un número: una unidad de prioridad fuera
    # pesa más que cualquier atraso posible.
    m = 1 + sum(t.peso * instancia.atraso_si_queda_fuera(t) for t in instancia.tareas)

    def escalar(par: Tuple[int, int]) -> int:
        return par[0] * m + par[1]

    actual = escalar(costo(instancia, decodificar(instancia, orden)))
    mejor_orden, mejor = list(orden), actual
    temperatura = temperatura_inicial

    if n >= 2:
        for _ in range(iteraciones):
            vecino = list(orden)
            i, j = rng.sample(range(n), 2)
            if rng.random() < 0.5:
                vecino[i], vecino[j] = vecino[j], vecino[i]
            else:
                vecino.insert(j, vecino.pop(i))
            nuevo = escalar(costo(instancia, decodificar(instancia, vecino)))
            delta = nuevo - actual
            if delta <= 0 or rng.random() < math.exp(-delta / max(temperatura, 1e-9)):
                orden, actual = vecino, nuevo
                if actual < mejor:
                    mejor_orden, mejor = list(orden), actual
            temperatura *= enfriamiento

    inicios = decodificar(instancia, mejor_orden)
    return construir_resultado(
        instancia, inicios, "RECOCIDO", time.perf_counter() - reloj
    )


# ---------- Cota inferior por relajación ----------

def cota_inferior(instancia: Instancia) -> int:
    """Atraso efectivo mínimo si cada tarea tuviera el tiempo libre para ella
    sola (sin la restricción de no solaparse). Ningún método puede bajar de
    aquí: es una cota del nivel 2 del objetivo."""
    total = 0
    for t in instancia.tareas:
        validos = instancia.bloques_validos(t)
        if not validos:
            # No cabe en ningún bloque: queda fuera con cualquier método.
            total += t.peso * instancia.atraso_si_queda_fuera(t)
            continue
        fin_mas_temprano = min(b.inicio + t.duracion for b in validos)
        total += t.peso * max(0, fin_mas_temprano - t.limite)
    return total
