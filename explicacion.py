"""US-C6: explica en texto qué cambió entre dos agendas y por qué.

No usa ningún modelo de lenguaje: son plantillas sobre el diff de las agendas.
Las agendas pueden ser objetos PlanificarResponse o diccionarios con la misma forma.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _campo(obj: Any, nombre: str, defecto: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(nombre, defecto)
    return getattr(obj, nombre, defecto)


def _a_datetime(valor: Any) -> datetime:
    if isinstance(valor, datetime):
        return valor
    return datetime.fromisoformat(valor)


def _fmt_duracion(minutos: int) -> str:
    horas, resto = divmod(abs(int(minutos)), 60)
    if horas and resto:
        return f"{horas} h {resto} min"
    if horas:
        return f"{horas} h"
    return f"{resto} min"


def _fmt_hora(dt: datetime) -> str:
    hora12 = dt.hour % 12 or 12
    sufijo = "am" if dt.hour < 12 else "pm"
    articulo = "la" if hora12 == 1 else "las"
    return f"{articulo} {hora12}:{dt.minute:02d} {sufijo}"


def _fmt_dia(dt: datetime) -> str:
    return f"{_DIAS[dt.weekday()]} {dt.day}"


def _fmt_cambio(antes: datetime, despues: datetime) -> str:
    if antes.date() == despues.date():
        return f"de {_fmt_hora(antes)} a {_fmt_hora(despues)}"
    return (
        f"del {_fmt_dia(antes)} a {_fmt_hora(antes)} "
        f"al {_fmt_dia(despues)} a {_fmt_hora(despues)}"
    )


def _nombre(tarea_id: int, nombres: Optional[Dict[int, str]]) -> str:
    if nombres and nombres.get(tarea_id):
        return f'"{nombres[tarea_id]}"'
    return f"la tarea #{tarea_id}"


def _unir(partes: List[str]) -> str:
    if len(partes) <= 1:
        return "".join(partes)
    return ", ".join(partes[:-1]) + " y " + partes[-1]


def _inicios(agenda: Any) -> Dict[int, datetime]:
    return {
        _campo(item, "tarea_id"): _a_datetime(_campo(item, "inicio"))
        for item in (_campo(agenda, "agenda", []) or [])
    }


def _riesgos(agenda: Any) -> Dict[int, int]:
    return {
        _campo(item, "tarea_id"): _campo(item, "holgura_min")
        for item in (_campo(agenda, "en_riesgo", []) or [])
    }


def generar_explicacion(
    tarea_terminada: Any,
    duracion_estimada: int,
    duracion_real: int,
    agenda_vieja: Any,
    agenda_nueva: Any,
    nombres: Optional[Dict[int, str]] = None,
) -> str:
    """Devuelve el texto que explica la replanificación.

    tarea_terminada: objeto o dict con `id` y `tipo` (o directamente un texto).
    duracion_estimada / duracion_real: minutos.
    agenda_vieja / agenda_nueva: respuestas del planificador (antes y después).
    nombres: opcional, {tarea_id: tipo} para nombrar las tareas que se movieron.
    """
    frases: List[str] = []

    # 1. ¿Tardó más o menos de lo estimado?
    if isinstance(tarea_terminada, str):
        sujeto = tarea_terminada
    else:
        nombre = _campo(tarea_terminada, "nombre")
        tarea_id = _campo(tarea_terminada, "id")
        sujeto = f'"{nombre}"' if nombre else f"La tarea #{tarea_id}"

    diferencia = duracion_real - duracion_estimada
    if diferencia > 0:
        frases.append(
            f"{sujeto} tardó {_fmt_duracion(duracion_real)} en vez de "
            f"{_fmt_duracion(duracion_estimada)} ({_fmt_duracion(diferencia)} más de lo estimado)."
        )
        conector = "Por eso moví"
    elif diferencia < 0:
        frases.append(
            f"{sujeto} tardó {_fmt_duracion(duracion_real)} en vez de "
            f"{_fmt_duracion(duracion_estimada)} ({_fmt_duracion(diferencia)} menos de lo estimado)."
        )
        conector = "Con el tiempo que sobró moví"
    else:
        frases.append(f"{sujeto} tardó {_fmt_duracion(duracion_real)}, justo lo estimado.")
        conector = "Moví"

    # 2. ¿Qué tareas futuras cambiaron de hora de inicio?
    inicios_viejos = _inicios(agenda_vieja)
    inicios_nuevos = _inicios(agenda_nueva)

    movidas = [
        f"{_nombre(tid, nombres)} {_fmt_cambio(inicios_viejos[tid], inicio)}"
        for tid, inicio in sorted(inicios_nuevos.items(), key=lambda par: par[1])
        if tid in inicios_viejos and inicios_viejos[tid] != inicio
    ]
    if movidas:
        frases.append(f"{conector} {_unir(movidas)}.")

    fuera = [_nombre(tid, nombres) for tid in inicios_viejos if tid not in inicios_nuevos]
    if fuera:
        frases.append(f"Ya no cabe en la agenda: {_unir(fuera)}.")

    # 3. ¿Qué tareas pasaron a estar en riesgo?
    riesgo_viejo = _riesgos(agenda_vieja)
    riesgo_nuevo = _riesgos(agenda_nueva)

    nuevas_en_riesgo = []
    for tid, holgura in riesgo_nuevo.items():
        if tid in riesgo_viejo:
            continue
        if holgura is not None and holgura < 0:
            detalle = f"le faltan {_fmt_duracion(holgura)} para cumplir su plazo"
        else:
            detalle = f"le quedan solo {_fmt_duracion(holgura or 0)} de holgura"
        nuevas_en_riesgo.append(f"{_nombre(tid, nombres)} ({detalle})")
    if nuevas_en_riesgo:
        frases.append(f"Ahora está en riesgo {_unir(nuevas_en_riesgo)}.")

    salen_de_riesgo = [_nombre(tid, nombres) for tid in riesgo_viejo if tid not in riesgo_nuevo]
    if salen_de_riesgo:
        frases.append(f"Ya no está en riesgo {_unir(salen_de_riesgo)}.")

    if not movidas and not fuera and not nuevas_en_riesgo and not salen_de_riesgo:
        frases.append("El resto de la agenda no cambió.")

    if _campo(agenda_nueva, "factible", True) is False:
        frases.append("No fue posible ubicar todas las tareas en los bloques libres.")

    return " ".join(frases)