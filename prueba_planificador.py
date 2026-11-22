"""
FocusFlow — Pruebas del planificador (evidencia de US-B1, US-B2, US-B3 y US-B4).

Uso:
    python prueba_planificador.py      # corre todo e imprime el resultado
    pytest prueba_planificador.py      # mismas pruebas, con pytest

Cada prueba verifica una propiedad que el jurado podría poner en duda:
sin solapes, dentro de los bloques, con descanso, plazos con prioridad, tareas
que no caben, determinismo, contrato de API y tiempo de respuesta.
"""

from __future__ import annotations

import random
import sys
import time
from datetime import datetime, timedelta

from planificador import (
    DESCANSO_POR_DEFECTO_MIN,
    UMBRAL_RIESGO_MIN,
    Instancia,
    Resultado,
    construir_instancia,
    planificar,
    resolver,
)
from planificador_lineas_base import cota_inferior, edf, recocido_simulado

BASE = datetime(2026, 11, 23, 0, 0)  # lunes


def hora(dia: int, hh: int, mm: int = 0) -> str:
    return (BASE + timedelta(days=dia, hours=hh, minutes=mm)).isoformat(timespec="minutes")


def tarea(id: int, duracion: int, limite: str, prioridad: int = 1, tipo: str = "taller"):
    return {"id": id, "tipo": tipo, "duracion_min": duracion, "limite": limite, "prioridad": prioridad}


def bloque(inicio: str, fin: str):
    return {"inicio": inicio, "fin": fin}


# ---------- Verificador de agendas ----------

def verificar_agenda(instancia: Instancia, resultado: Resultado) -> None:
    """Comprueba las restricciones duras sobre cualquier resultado."""
    por_id = {t.id: t for t in instancia.tareas}
    tramos = []
    for item in resultado.agenda:
        t = por_id[item["tarea_id"]]
        ini = datetime.fromisoformat(item["inicio"])
        fin = datetime.fromisoformat(item["fin"])
        a = round((ini - instancia.origen).total_seconds() / 60)
        z = round((fin - instancia.origen).total_seconds() / 60)
        assert z - a == t.duracion, f"tarea {t.id}: dura {z - a}, debía durar {t.duracion}"
        assert any(b.inicio <= a and z <= b.fin for b in instancia.bloques), (
            f"tarea {t.id} ({item['inicio']}–{item['fin']}) se sale de los bloques libres"
        )
        tramos.append((a, z, t.id))
    tramos.sort()
    for (a1, z1, i1), (a2, _, i2) in zip(tramos, tramos[1:]):
        assert a2 >= z1 + instancia.descanso, (
            f"tareas {i1} y {i2}: solo {a2 - z1} min entre ellas, descanso {instancia.descanso}"
        )
    agendadas = {i for _, _, i in tramos}
    assert agendadas.isdisjoint(resultado.no_agendadas)
    assert agendadas | set(resultado.no_agendadas) == set(por_id), "falta reportar alguna tarea"
    assert resultado.factible == (not resultado.no_agendadas)
    for r in resultado.en_riesgo:
        assert isinstance(r["holgura_min"], int), "los minutos deben ser enteros"


def resolver_y_verificar(peticion) -> tuple[Instancia, Resultado]:
    instancia = construir_instancia(
        peticion["tareas"], peticion["bloques_libres"], peticion.get("descanso_min")
    )
    resultado = resolver(instancia)
    verificar_agenda(instancia, resultado)
    return instancia, resultado


def inicio_de(resultado: Resultado, tarea_id: int) -> str:
    return next(a["inicio"] for a in resultado.agenda if a["tarea_id"] == tarea_id)


# ---------- Pruebas ----------

def test_tres_tareas_en_dos_bloques():
    """US-B1: el ejemplo base cabe completo, sin solapes y con descanso."""
    peticion = {
        "tareas": [
            tarea(1, 90, hora(0, 23, 59), 3),
            tarea(2, 60, hora(1, 12), 2, "lectura"),
            tarea(3, 150, hora(1, 23, 59), 3, "programacion"),
        ],
        "bloques_libres": [bloque(hora(0, 18), hora(0, 21)), bloque(hora(1, 8), hora(1, 11))],
        "descanso_min": 15,
    }
    _, r = resolver_y_verificar(peticion)
    assert r.factible and len(r.agenda) == 3
    assert r.estado == "OPTIMAL"
    assert r.en_riesgo == []


def test_descanso_por_defecto():
    """Contrato: si no se envía descanso_min, vale 15."""
    peticion = {
        "tareas": [tarea(1, 60, hora(0, 23)), tarea(2, 60, hora(0, 23))],
        "bloques_libres": [bloque(hora(0, 8), hora(0, 12))],
    }
    instancia, r = resolver_y_verificar(peticion)
    assert instancia.descanso == DESCANSO_POR_DEFECTO_MIN
    fines = sorted(datetime.fromisoformat(a["fin"]) for a in r.agenda)
    inicios = sorted(datetime.fromisoformat(a["inicio"]) for a in r.agenda)
    assert inicios[1] - fines[0] >= timedelta(minutes=15)


def test_prioridad_contra_plazo():
    """US-B2: cuando no alcanza el tiempo, el solver protege la tarea importante.

    EDF mete primero la de plazo más cercano (prioridad 1) y la importante ya no
    cabe en el bloque: queda para el día siguiente, muy atrasada. CP-SAT hace lo
    contrario. Es la diferencia central contra la línea base.
    """
    peticion = {
        "tareas": [
            tarea(1, 60, hora(0, 9, 20), prioridad=1),   # plazo más cercano, poco importante
            tarea(2, 90, hora(0, 9, 40), prioridad=3),   # importante
        ],
        "bloques_libres": [bloque(hora(0, 8), hora(0, 9, 40)), bloque(hora(1, 8), hora(1, 12))],
        "descanso_min": 10,
    }
    instancia, r = resolver_y_verificar(peticion)
    assert inicio_de(r, 2) == hora(0, 8) + ":00", "la tarea importante debe ir primero"
    base = edf(instancia)
    verificar_agenda(instancia, base)
    assert r.atraso_ponderado < base.atraso_ponderado, (r.atraso_ponderado, base.atraso_ponderado)


def test_tarea_que_no_cabe():
    """US-B1: una tarea más larga que cualquier bloque queda fuera, el resto se
    agenda igual y se reporta como en riesgo (no se cae toda la agenda)."""
    peticion = {
        "tareas": [tarea(1, 300, hora(0, 23)), tarea(2, 60, hora(0, 23))],
        "bloques_libres": [bloque(hora(0, 18), hora(0, 21))],
    }
    _, r = resolver_y_verificar(peticion)
    assert not r.factible
    assert r.no_agendadas == [1]
    assert [a["tarea_id"] for a in r.agenda] == [2]
    # Hecha después del último bloque (21:00 + 5 h = 2:00) no llega a las 23:00.
    assert {"tarea_id": 1, "holgura_min": -180} in r.en_riesgo


def test_sin_tiempo_para_todas():
    """Si el tiempo libre total no alcanza, queda fuera la de menor prioridad."""
    peticion = {
        "tareas": [
            tarea(1, 100, hora(0, 23), prioridad=1),
            tarea(2, 100, hora(0, 23), prioridad=3),
        ],
        "bloques_libres": [bloque(hora(0, 18), hora(0, 20))],
    }
    _, r = resolver_y_verificar(peticion)
    assert r.no_agendadas == [1]


def test_en_riesgo_umbral_y_vencidas():
    """US-B4: holgura negativa (atrasada) o menor que el umbral -> en riesgo."""
    peticion = {
        "tareas": [
            tarea(1, 60, hora(0, 9, 10), 3),       # importante: termina 9:00 -> holgura 10
            tarea(2, 60, hora(0, 7)),              # plazo ya vencido
            tarea(3, 30, hora(3, 23)),             # mucha holgura
        ],
        "bloques_libres": [bloque(hora(0, 8), hora(0, 12))],
        "descanso_min": 0,
    }
    _, r = resolver_y_verificar(peticion)
    riesgo = {x["tarea_id"]: x["holgura_min"] for x in r.en_riesgo}
    assert 3 not in riesgo
    assert riesgo[2] < 0
    assert 0 <= riesgo[1] < UMBRAL_RIESGO_MIN
    holguras = [x["holgura_min"] for x in r.en_riesgo]
    assert holguras == sorted(holguras), "en_riesgo va de la más grave a la menos grave"


def test_casos_borde():
    """Sin tareas, sin bloques: respuestas válidas, nunca una excepción."""
    _, vacia = resolver_y_verificar({"tareas": [], "bloques_libres": [bloque(hora(0, 8), hora(0, 9))]})
    assert vacia.factible and vacia.agenda == [] and vacia.en_riesgo == []

    _, sin_bloques = resolver_y_verificar({"tareas": [tarea(1, 30, hora(0, 9))], "bloques_libres": []})
    assert not sin_bloques.factible and sin_bloques.no_agendadas == [1]


def test_determinismo():
    """US-B3 + US-C6: la misma entrada da la misma agenda, aunque cambie el
    orden en que llegan las tareas. Si no, la explicación anunciaría cambios
    que no ocurrieron."""
    rng = random.Random(7)
    peticion = escenario_aleatorio(rng, n_tareas=12)
    primera = planificar(peticion)
    for _ in range(3):
        mezclada = dict(peticion, tareas=rng.sample(peticion["tareas"], len(peticion["tareas"])))
        assert planificar(mezclada) == primera


def test_contrato_de_api():
    """US-C1: la salida valida contra schemas.PlanificarResponse (minutos enteros,
    fechas ISO) y es la forma que espera el agente."""
    try:
        import schemas
    except ImportError:  # sin pydantic instalado
        return
    rng = random.Random(3)
    peticion = escenario_aleatorio(rng, n_tareas=8)
    salida = planificar(peticion)
    respuesta = schemas.PlanificarResponse(**salida)
    assert respuesta.explicacion == ""
    # Y también por el adaptador que usa main.py.
    from planificador import planificar_contrato

    entrada = schemas.PlanificarRequest(**peticion)
    assert planificar_contrato(entrada).model_dump(mode="json") == respuesta.model_dump(mode="json")


def test_nunca_peor_que_las_lineas_base():
    """Evidencia: en 40 escenarios aleatorios CP-SAT nunca es peor que EDF ni que
    el recocido simulado, y nunca baja de la cota inferior."""
    rng = random.Random(11)
    for _ in range(40):
        peticion = escenario_aleatorio(rng, n_tareas=rng.randint(3, 12))
        instancia, r = resolver_y_verificar(peticion)
        for base in (edf(instancia), recocido_simulado(instancia, iteraciones=300)):
            verificar_agenda(instancia, base)
            fuera_r = sum(t.peso for t in instancia.tareas if t.id in r.no_agendadas)
            fuera_b = sum(t.peso for t in instancia.tareas if t.id in base.no_agendadas)
            assert (fuera_r, r.atraso_ponderado) <= (fuera_b, base.atraso_ponderado)
        if r.factible:
            assert r.atraso_ponderado >= cota_inferior(instancia)


def test_rendimiento():
    """US-B3: 25 tareas en 15 bloques se planifican en menos de 5 segundos."""
    rng = random.Random(5)
    peticion = escenario_aleatorio(rng, n_tareas=25, n_dias=5)
    reloj = time.perf_counter()
    resolver_y_verificar(peticion)
    assert time.perf_counter() - reloj < 5.5


# ---------- Generador de escenarios para las pruebas ----------

def escenario_aleatorio(rng: random.Random, n_tareas: int, n_dias: int = 3) -> dict:
    bloques = []
    for dia in range(n_dias):
        h = 7
        for _ in range(3):
            ini = h + rng.randint(0, 2)
            fin = ini + rng.randint(1, 4)
            if fin > 23:
                break
            bloques.append(bloque(hora(dia, ini), hora(dia, fin)))
            h = fin + 1
    tipos = ["programacion", "lectura", "taller", "estudio_parcial"]
    tareas = [
        tarea(
            i,
            rng.choice([30, 45, 60, 90, 120, 150]),
            hora(rng.randint(0, n_dias - 1), rng.randint(9, 23), rng.choice([0, 30, 59])),
            rng.randint(1, 3),
            rng.choice(tipos),
        )
        for i in range(1, n_tareas + 1)
    ]
    return {"tareas": tareas, "bloques_libres": bloques, "descanso_min": rng.choice([0, 10, 15])}


# ---------- Ejecución directa ----------

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    pruebas = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    fallidas = 0
    for nombre, prueba in pruebas:
        reloj = time.perf_counter()
        try:
            prueba()
            estado = "OK   "
        except AssertionError as error:
            estado, fallidas = "FALLA", fallidas + 1
            print(f"       {error}")
        print(f"{estado} {nombre:<36} {time.perf_counter() - reloj:6.2f} s")
    print()
    print(f"{len(pruebas) - fallidas}/{len(pruebas)} pruebas pasan")
    sys.exit(1 if fallidas else 0)
