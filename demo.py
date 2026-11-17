"""US-C7: demo reproducible de FocusFlow.

Uso:
    1. En una terminal:  uvicorn main:app
    2. En otra terminal: python demo.py

Requiere: pip install requests
Para que la tarea atrasada sea la #1, arranca con una base de datos limpia.
"""
import sys
import textwrap
from datetime import date, datetime, time, timedelta

import requests

BASE_URL = "http://127.0.0.1:8000"
ANCHO = 70
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ---------- utilidades ----------

def llamar(metodo: str, ruta: str, **kwargs):
    try:
        respuesta = requests.request(metodo, BASE_URL + ruta, timeout=10, **kwargs)
    except requests.exceptions.ConnectionError:
        sys.exit(
            f"\nNo hay servidor en {BASE_URL}.\n"
            "Levántalo en otra terminal con:  uvicorn main:app\n"
        )
    if not respuesta.ok:
        sys.exit(f"\nError {respuesta.status_code} en {metodo} {ruta}:\n{respuesta.text}\n")
    return respuesta.json()


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def fmt_duracion(minutos: int) -> str:
    horas, resto = divmod(abs(int(minutos)), 60)
    if horas and resto:
        return f"{horas} h {resto} min"
    if horas:
        return f"{horas} h"
    return f"{resto} min"


def fmt_dia(dt: datetime) -> str:
    return f"{DIAS[dt.weekday()]} {dt.day:02d}"


def titulo(texto: str) -> None:
    print()
    print("=" * ANCHO)
    print(f"  {texto}")
    print("=" * ANCHO)


def paso(numero: int, texto: str) -> None:
    print()
    print(f"[{numero}/4] {texto}")
    print("-" * ANCHO)


# ---------- escenario ----------

def main() -> None:
    hoy = date.today()
    fin_del_dia = time(23, 59)

    tareas_demo = [
        {
            "nombre": "Taller de Cálculo",
            "tipo": "taller",
            "limite": iso(datetime.combine(hoy, fin_del_dia)),
            "prioridad": 3,
            "estimacion_usuario_min": 90,
        },
        {
            "nombre": "Lectura de Estocásticos",
            "tipo": "lectura",
            "limite": iso(datetime.combine(hoy + timedelta(days=1), fin_del_dia)),
            "prioridad": 2,
            "estimacion_usuario_min": 60,
        },
        {
            "nombre": "Proyecto de Sistemas Inteligentes",
            "tipo": "programacion",
            "limite": iso(datetime.combine(hoy + timedelta(days=2), fin_del_dia)),
            "prioridad": 3,
            "estimacion_usuario_min": 120,
        },
    ]

    titulo("FocusFlow - demo: tarea atrasada y replanificación automática")

    # 1. Crear las 3 tareas
    paso(1, "Registrar 3 tareas")
    creadas = [llamar("POST", "/tareas", json=t) for t in tareas_demo]
    for t in creadas:
        limite = datetime.fromisoformat(t["limite"])
        print(
            f"  #{t['id']:<3} {t['nombre']:<36}"
            f"{fmt_duracion(t['estimacion_usuario_min']):<12}"
            f"límite {fmt_dia(limite)} {limite:%H:%M}   prioridad {t['prioridad']}"
        )

    # 2. Bloque libre de hoy
    paso(2, "Declarar el bloque libre de hoy")
    bloques = llamar(
        "POST",
        "/bloques-libres",
        json={"dia": hoy.isoformat(), "bloques": [{"inicio": "14:00", "fin": "22:00"}]},
    )
    for b in bloques["bloques"]:
        inicio = datetime.fromisoformat(b["inicio"])
        fin = datetime.fromisoformat(b["fin"])
        print(f"  {fmt_dia(inicio)}   {inicio:%H:%M} - {fin:%H:%M}")

    # 3. Simular el atraso: la primera tarea tarda el doble de lo estimado
    atrasada = creadas[0]
    estimado = atrasada["estimacion_usuario_min"]
    real = estimado * 2
    inicio_real = datetime.combine(hoy, time(14, 0))
    fin_real = inicio_real + timedelta(minutes=real)

    paso(3, "Simular el atraso")
    print(f"  Tarea:     #{atrasada['id']} {atrasada['nombre']}")
    print(f"  Estimado:  {fmt_duracion(estimado)}")
    print(f"  Real:      {fmt_duracion(real)}  ({inicio_real:%H:%M} - {fin_real:%H:%M})")

    resultado = llamar(
        "POST",
        f"/tareas/{atrasada['id']}/terminar",
        json={"inicio": iso(inicio_real), "fin": iso(fin_real)},
    )

    # 4. Explicación y nueva agenda
    nombres = {t["id"]: t["nombre"] for t in llamar("GET", "/tareas")}

    def nombre(tarea_id: int) -> str:
        return nombres.get(tarea_id, f"Tarea #{tarea_id}")

    paso(4, "El agente replanifica solo y explica qué hizo")

    print("  EXPLICACIÓN")
    print()
    print(textwrap.fill(
        resultado["explicacion"], width=ANCHO - 4,
        initial_indent="    ", subsequent_indent="    ",
    ))

    print()
    print("  NUEVA AGENDA" + ("" if resultado["factible"] else "  (no factible)"))
    print()
    if not resultado["agenda"]:
        print("    (sin tareas agendadas)")
    for item in sorted(resultado["agenda"], key=lambda i: i["inicio"]):
        inicio = datetime.fromisoformat(item["inicio"])
        fin = datetime.fromisoformat(item["fin"])
        print(f"    {fmt_dia(inicio)}   {inicio:%H:%M} - {fin:%H:%M}   {nombre(item['tarea_id'])}")

    print()
    print("  EN RIESGO")
    print()
    if not resultado["en_riesgo"]:
        print("    Ninguna tarea en riesgo.")
    for item in resultado["en_riesgo"]:
        holgura = item["holgura_min"]
        if holgura < 0:
            detalle = f"le faltan {fmt_duracion(holgura)} para cumplir su plazo"
        else:
            detalle = f"solo {fmt_duracion(holgura)} de holgura"
        print(f"    {nombre(item['tarea_id'])}: {detalle}")

    print()
    print("=" * ANCHO)
    print()


if __name__ == "__main__":
    main()