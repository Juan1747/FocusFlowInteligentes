"""
FocusFlow — Evaluación del planificador contra sus líneas base (criterio de Evidencia).

Uso:
    python evaluacion_planificador.py              # imprime la tabla de resultados
    python evaluacion_planificador.py --guardar    # además escribe docs/evaluacion_planificador.md

Qué compara, sobre los MISMOS escenarios:
    EDF        línea base: ordenar por fecha límite (búsqueda voraz, semana 3)
    Recocido   alternativa descartada: búsqueda local (semana 3)
    CP-SAT     el planificador de FocusFlow
    Cota       relajación: el mejor atraso posible si las tareas no compitieran

Partición honesta (semana 4: train/valid/test). Los escenarios con semilla 0-49
son de AJUSTE: con ellos se eligieron los parámetros (tiempo por fase,
iteraciones y temperatura del recocido, umbral de riesgo). Los resultados se
reportan solo sobre los escenarios 50-199, de PRUEBA, que no se usaron para
ajustar nada.

Los escenarios imitan la semana de un estudiante: bloques libres por la tarde y
la noche, tareas de 30 min a 4 h según el tipo, plazos dentro de la semana y una
"presión" (minutos de tareas / minutos libres) entre 0.6 y 1.3.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

from planificador import Instancia, Resultado, construir_instancia, resolver
from planificador_lineas_base import cota_inferior, edf, recocido_simulado

LUNES = datetime(2026, 11, 23)
SEMILLAS_AJUSTE = range(0, 50)
SEMILLAS_PRUEBA = range(50, 200)

# Duración típica por tipo (minutos): (mínimo, máximo).
DURACION_POR_TIPO = {
    "programacion": (60, 240),
    "lectura": (30, 120),
    "taller": (45, 180),
    "estudio_parcial": (60, 180),
}


# ---------- Generador de escenarios ----------

def generar_escenario(semilla: int) -> dict:
    rng = random.Random(semilla)
    dias = rng.randint(3, 6)
    presion = rng.uniform(0.6, 1.3)

    bloques = []
    for d in range(dias):
        dia = LUNES + timedelta(days=d)
        # Tarde y/o noche, como un estudiante con clases en la mañana.
        if rng.random() < 0.8:
            ini = rng.choice([13, 14, 15])
            bloques.append((dia.replace(hour=ini), dia.replace(hour=ini + rng.randint(2, 4))))
        if rng.random() < 0.8:
            ini = rng.choice([18, 19])
            bloques.append((dia.replace(hour=ini), dia.replace(hour=ini) + timedelta(minutes=rng.choice([120, 150, 180]))))
    if not bloques:
        bloques.append((LUNES.replace(hour=18), LUNES.replace(hour=21)))
    libres = sum((z - a).total_seconds() / 60 for a, z in bloques)

    tareas, ocupado, tid = [], 0.0, 1
    while ocupado < presion * libres:
        tipo = rng.choice(list(DURACION_POR_TIPO))
        lo, hi = DURACION_POR_TIPO[tipo]
        duracion = rng.randrange(lo, hi + 1, 15)
        dia_limite = rng.randint(0, dias - 1)
        limite = LUNES + timedelta(days=dia_limite, hours=rng.choice([12, 18, 23]), minutes=59 if rng.random() < 0.5 else 0)
        tareas.append({
            "id": tid, "tipo": tipo, "duracion_min": duracion,
            "limite": limite.isoformat(timespec="minutes"), "prioridad": rng.randint(1, 3),
        })
        ocupado += duracion
        tid += 1

    return {
        "tareas": tareas,
        "bloques_libres": [
            {"inicio": a.isoformat(timespec="minutes"), "fin": z.isoformat(timespec="minutes")}
            for a, z in bloques
        ],
        "descanso_min": 15,
        "presion": presion,
    }


# ---------- Métricas ----------

@dataclass
class Metricas:
    atraso_efectivo: int # atraso ponderado, contando las no agendadas (ver medir)
    a_tiempo: float      # fracción de TODAS las tareas que terminan antes del plazo
    agendadas: float     # fracción de tareas que cupieron
    prioridad_fuera: int # suma de prioridades de las tareas que no cupieron
    segundos: float


def medir(instancia: Instancia, r: Resultado) -> Metricas:
    """Atraso efectivo (`planificador.atraso_efectivo`): el atraso ponderado de
    las tareas agendadas MÁS el de las que no cupieron, como si se hicieran justo
    después del último bloque libre. Sin esto, un método que deja tareas fuera
    parecería tener menos atraso que uno que las agenda todas."""
    n = len(instancia.tareas)
    a_tiempo = sum(
        1 for t in instancia.tareas
        if r.inicios.get(t.id) is not None and r.inicios[t.id] + t.duracion <= t.limite
    )
    return Metricas(
        atraso_efectivo=r.atraso_ponderado,
        a_tiempo=a_tiempo / n if n else 1.0,
        agendadas=(n - len(r.no_agendadas)) / n if n else 1.0,
        prioridad_fuera=sum(t.peso for t in instancia.tareas if t.id in r.no_agendadas),
        segundos=r.segundos,
    )


def _comparar(a: Metricas, b: Metricas) -> str:
    """Compara como el objetivo del solver: primero la prioridad que quedó
    fuera, después el atraso efectivo."""
    clave_a, clave_b = (a.prioridad_fuera, a.atraso_efectivo), (b.prioridad_fuera, b.atraso_efectivo)
    return "mejor" if clave_a < clave_b else "igual" if clave_a == clave_b else "peor"


def evaluar(semillas, verbose: bool = False) -> dict:
    metodos = ("EDF", "Recocido", "CP-SAT")
    filas: Dict[str, List[Metricas]] = {m: [] for m in metodos}
    cotas, optimos, n_tareas = [], 0, []
    contra = {base: {"mejor": 0, "igual": 0, "peor": 0} for base in ("EDF", "Recocido")}

    for k, semilla in enumerate(semillas):
        esc = generar_escenario(semilla)
        inst = construir_instancia(esc["tareas"], esc["bloques_libres"], esc["descanso_min"])
        n_tareas.append(len(inst.tareas))
        resultados = {
            "EDF": edf(inst),
            "Recocido": recocido_simulado(inst, semilla=semilla),
            "CP-SAT": resolver(inst),
        }
        for m, r in resultados.items():
            filas[m].append(medir(inst, r))
        cotas.append(cota_inferior(inst))
        optimos += resultados["CP-SAT"].estado == "OPTIMAL"
        for base in contra:
            contra[base][_comparar(filas["CP-SAT"][-1], filas[base][-1])] += 1
        if verbose:
            print(f"\r  escenario {k + 1}/{len(semillas)}", end="", file=sys.stderr, flush=True)
    if verbose:
        print(file=sys.stderr)

    return {
        "filas": filas, "cotas": cotas, "optimos": optimos, "contra": contra,
        "semillas": semillas, "n_tareas": n_tareas,
    }


def p95(valores: List[float]) -> float:
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(0.95 * len(ordenados)))]


def tabla(res: dict) -> str:
    lineas = [
        "| Método | Atraso efectivo medio (min ponderados) | Tareas a tiempo | Tareas agendadas | Prioridad que quedó fuera (media) | Tiempo p95 (s) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for m, filas in res["filas"].items():
        lineas.append(
            f"| {m} | {statistics.mean(f.atraso_efectivo for f in filas):,.0f} "
            f"| {100 * statistics.mean(f.a_tiempo for f in filas):.1f} % "
            f"| {100 * statistics.mean(f.agendadas for f in filas):.1f} % "
            f"| {statistics.mean(f.prioridad_fuera for f in filas):.2f} "
            f"| {p95([f.segundos for f in filas]):.2f} |"
        )
    lineas.append(
        f"| Cota inferior (relajación) | {statistics.mean(res['cotas']):,.0f} | — | — | — | — |"
    )
    return "\n".join(lineas)


def resumen(res: dict) -> str:
    semillas = res["semillas"]
    n = len(semillas)
    filas = res["filas"]
    media = {m: statistics.mean(f.atraso_efectivo for f in fs) for m, fs in filas.items()}
    cota = statistics.mean(res["cotas"])

    def reduccion(base: str) -> tuple[float, int]:
        """Reducción del atraso solo donde ambos dejan fuera la misma prioridad:
        si uno agenda más tareas importantes, su atraso no es comparable."""
        pares = [
            (c.atraso_efectivo, b.atraso_efectivo)
            for c, b in zip(filas["CP-SAT"], filas[base])
            if c.prioridad_fuera == b.prioridad_fuera
        ]
        total_b = sum(b for _, b in pares)
        return (100 * (1 - sum(c for c, _ in pares) / total_b) if total_b else 0.0), len(pares)

    def brecha(m: str) -> float:
        return media[m] - cota

    lineas = [
        f"- {n} escenarios (semillas {semillas.start}–{semillas.stop - 1}), "
        f"de {min(res['n_tareas'])} a {max(res['n_tareas'])} tareas cada uno.",
    ]
    for base, v in res["contra"].items():
        red, comparables = reduccion(base)
        lineas.append(
            f"- CP-SAT frente a {base}: mejor en {v['mejor']}, igual en {v['igual']}, peor en {v['peor']} "
            f"(primero la prioridad que queda fuera, luego el atraso). En los {comparables} escenarios "
            f"donde ambos dejan fuera la misma prioridad, CP-SAT tiene {red:.0f} % menos atraso efectivo."
        )
    if brecha("EDF") > 0:
        lineas.append(
            f"- De la distancia entre EDF y la cota inferior, CP-SAT cierra el "
            f"{100 * (1 - brecha('CP-SAT') / brecha('EDF')):.0f} %."
        )
    lineas.append(
        f"- CP-SAT demostró optimalidad en {res['optimos']} de {n} escenarios "
        f"({100 * res['optimos'] / n:.0f} %) dentro del límite de 5 s; en el resto entrega la mejor agenda encontrada."
    )
    return "\n".join(lineas)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--guardar", action="store_true", help="escribe docs/evaluacion_planificador.md")
    parser.add_argument("--ajuste", action="store_true", help="evalúa sobre los escenarios de ajuste")
    args = parser.parse_args()

    semillas = SEMILLAS_AJUSTE if args.ajuste else SEMILLAS_PRUEBA
    print(f"Evaluando {len(semillas)} escenarios de {'ajuste' if args.ajuste else 'prueba'}...", file=sys.stderr)
    res = evaluar(semillas, verbose=True)
    texto = tabla(res) + "\n\n" + resumen(res)
    print(texto)

    if args.guardar and not args.ajuste:
        destino = Path(__file__).parent / "docs" / "evaluacion_planificador.md"
        destino.parent.mkdir(exist_ok=True)
        destino.write_text(
            "# Evaluación del planificador\n\n"
            "Generado con `python evaluacion_planificador.py --guardar`. "
            "Metodología y lectura de los resultados en [`planificador.md`](planificador.md#5-evidencia-contra-la-línea-base).\n\n"
            + texto + "\n",
            encoding="utf-8",
        )
        print(f"\nGuardado en {destino}", file=sys.stderr)


if __name__ == "__main__":
    main()
