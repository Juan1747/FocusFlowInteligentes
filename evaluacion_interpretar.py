"""
FocusFlow — Evaluación de POST /interpretar (frase -> tarea).

Compara, sobre las mismas frases de datos/frases_interpretar.json:
    reglas   intérprete por palabras clave y expresiones de fecha (línea base, sin LLM)
    claude   Claude Haiku 5.5 con salida estructurada       (si hay ANTHROPIC_API_KEY)
    gemini   Gemini Flash-Lite (plan gratuito)              (si hay GEMINI_API_KEY)
    ollama   modelo local en Ollama                          (si está corriendo)

Métrica por campo (exactitud): tipo, fecha, hora, prioridad, estimación y si el
nombre contiene la palabra clave. "Tarea completa" = todos los campos bien.
Todas las salidas pasan por la misma validación que usa la app.

Ejecutar:
    python evaluacion_interpretar.py                    # conjunto ciego, métodos disponibles
    python evaluacion_interpretar.py --conjunto ajuste  # frases usadas para afinar
    python evaluacion_interpretar.py --metodos reglas ollama --guardar
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from datetime import datetime
from pathlib import Path

import asistente

RAIZ = Path(__file__).parent
DATOS = RAIZ / "datos" / "frases_interpretar.json"
SALIDA = RAIZ / "docs" / "evaluacion_interpretar.md"
CAMPOS = ["tipo", "fecha", "hora", "prioridad", "estimacion_min", "nombre"]
ETIQUETAS = {"tipo": "Tipo", "fecha": "Fecha", "hora": "Hora", "prioridad": "Prioridad",
             "estimacion_min": "Estimación", "nombre": "Nombre"}


def proveedor_para(metodo: str):
    if metodo == "reglas":
        return None
    if metodo == "claude":
        return asistente.ProveedorClaude()
    if metodo == "gemini":
        return asistente.ProveedorGemini()
    if metodo == "ollama":
        if not asistente.ProveedorOllama.disponible():
            raise SystemExit(f"Ollama no está corriendo o no tiene el modelo {asistente.MODELO_OLLAMA}.")
        return asistente.ProveedorOllama()
    raise SystemExit(f"Método desconocido: {metodo}")


def metodos_disponibles() -> list[str]:
    metodos = ["reglas"]
    try:
        import anthropic  # noqa: F401
        import os
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            metodos.append("claude")
    except ImportError:
        pass
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        metodos.append("gemini")
    if asistente.ProveedorOllama.disponible():
        metodos.append("ollama")
    return metodos


def comparar(salida: asistente.InterpretarResponse, esperado: dict) -> dict[str, bool]:
    t = salida.tarea
    return {
        "tipo": t.tipo == esperado["tipo"],
        "fecha": (t.limite.date().isoformat() if t.limite else None) == esperado["fecha"],
        "hora": (t.limite.strftime("%H:%M") if t.limite else None) == esperado["hora"],
        "prioridad": t.prioridad == esperado["prioridad"],
        "estimacion_min": t.estimacion_usuario_min == esperado["estimacion_min"],
        "nombre": esperado["clave_nombre"] in asistente.sin_tildes(t.nombre),
    }


def evaluar(metodo: str, frases: list[dict], ahora: datetime, detalle: bool, pausa: float = 0.0) -> dict:
    proveedor = proveedor_para(metodo)
    aciertos = {c: 0 for c in CAMPOS}
    completas, tiempos, errores, avisos = 0, [], [], 0
    for i, f in enumerate(frases):
        if pausa and i:
            time.sleep(pausa)  # respeta el límite por minuto de los planes gratuitos
        inicio = time.perf_counter()
        salida = asistente.interpretar(f["texto"], ahora, proveedor)
        tiempos.append(time.perf_counter() - inicio)
        if proveedor is not None and salida.fuente == "reglas":
            avisos += 1  # el LLM falló y se usó el respaldo
        resultado = comparar(salida, f["esperado"])
        for campo, ok in resultado.items():
            aciertos[campo] += ok
        if all(resultado.values()):
            completas += 1
        else:
            errores.append((f, salida, [c for c, ok in resultado.items() if not ok]))
    if detalle and errores:
        print(f"\n--- Errores de {metodo} ---")
        for f, salida, malos in errores:
            t = salida.tarea
            print(f"#{f['id']:>2} {f['texto']}\n     falla: {', '.join(malos)} | obtuvo: tipo={t.tipo} "
                  f"limite={t.limite} prioridad={t.prioridad} est={t.estimacion_usuario_min} nombre={t.nombre!r}")
    n = len(frases)
    return {
        "metodo": metodo,
        "modelo": proveedor.modelo("interpretar") if proveedor else "sin LLM",
        "n": n,
        "campos": {c: aciertos[c] / n for c in CAMPOS},
        "completas": completas / n,
        "mediana_s": statistics.median(tiempos),
        "respaldos": avisos,
    }


def tabla(resultados: list[dict]) -> str:
    encabezado = "| Método | " + " | ".join(ETIQUETAS[c] for c in CAMPOS) + " | **Tarea completa** | Tiempo mediano |"
    lineas = [encabezado, "|---|" + "---:|" * (len(CAMPOS) + 2)]
    for r in resultados:
        nombre = r["metodo"] if r["metodo"] == "reglas" else f"{r['metodo']} ({r['modelo']})"
        celdas = " | ".join(f"{r['campos'][c]:.0%}" for c in CAMPOS)
        lineas.append(f"| {nombre} | {celdas} | **{r['completas']:.0%}** | {r['mediana_s']:.2f} s |")
    return "\n".join(lineas)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--conjunto", choices=["ciego", "prueba", "ajuste", "todo"], default="ciego")
    parser.add_argument("--metodos", nargs="+", choices=["reglas", "claude", "gemini", "ollama"])
    parser.add_argument("--pausa", type=float, default=4.5,
                        help="segundos entre llamadas a Gemini (plan gratuito: ~15 por minuto)")
    parser.add_argument("--detalle", action="store_true", help="imprime cada frase mal interpretada")
    parser.add_argument("--guardar", action="store_true", help=f"escribe {SALIDA.relative_to(RAIZ)}")
    args = parser.parse_args()

    datos = json.loads(DATOS.read_text(encoding="utf-8"))
    ahora = datetime.fromisoformat(datos["ahora"])
    frases = [f for f in datos["frases"] if args.conjunto == "todo" or f["conjunto"] == args.conjunto]
    metodos = args.metodos or metodos_disponibles()

    resultados = [evaluar(m, frases, ahora, args.detalle, args.pausa if m == "gemini" else 0.0)
                  for m in metodos]
    texto = tabla(resultados)
    print(f"\nConjunto: {args.conjunto} ({len(frases)} frases) · referencia: {datos['ahora']}\n")
    print(texto)
    for r in resultados:
        if r["respaldos"]:
            print(f"\nAviso: {r['metodo']} falló en {r['respaldos']} frases y se usó el intérprete por reglas.")

    if args.guardar:
        SALIDA.write_text(
            "# Evaluación del intérprete de tareas\n\n"
            "Generado con `python evaluacion_interpretar.py --guardar`. Metodología en "
            "[`asistente.md`](asistente.md#evidencia).\n\n"
            f"Conjunto **{args.conjunto}**: {len(frases)} frases de `datos/frases_interpretar.json`, "
            f"con fecha de referencia {datos['ahora']}.\n\n" + texto + "\n",
            encoding="utf-8",
        )
        print(f"\nGuardado en {SALIDA.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
