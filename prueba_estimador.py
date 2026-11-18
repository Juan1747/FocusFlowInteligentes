"""
FocusFlow — Prueba de convergencia del estimador (evidencia de US-A2 y US-A3).

No es un extra: el backlog pide explícitamente un script que meta ~20 observaciones
simuladas de un tipo de tarea y muestre cómo la predicción se va acercando al
promedio real a medida que el estimador aprende.

Qué demuestra, de cara al jurado:
  US-A2  Al registrar duraciones reales, la media y la incertidumbre cambian de
         forma verificable.
  US-A3  Con 0 observaciones la predicción ES la estimación del usuario; con muchas,
         se acerca al promedio real, aunque el usuario siga estimando mal.

Escenario: un usuario que SUBESTIMA "programacion". Dice que le toma 90 min, pero en
realidad le toma ~160 min (con ruido). Sin aprendizaje, la app repetiría 90 para
siempre. Con el estimador, la predicción debe migrar de 90 hacia ~160.

Ejecutar:
    python prueba_estimador.py
"""

import random

from estimador import Estimador

# Semilla fija: la prueba es reproducible (mismo resultado en cada corrida).
random.seed(42)

TIPO = "programacion"
ESTIMACION_USUARIO = 90       # lo que el usuario cree que tarda (minutos)
DURACION_REAL_MEDIA = 160     # lo que de verdad tarda, en promedio (minutos)
RUIDO = 25                    # dispersión de las duraciones reales (minutos)
N_OBSERVACIONES = 20


def duracion_simulada() -> int:
    """Una duración real ruidosa alrededor de la media verdadera."""
    return max(1, round(random.gauss(DURACION_REAL_MEDIA, RUIDO)))


def main() -> None:
    est = Estimador()

    print("=" * 74)
    print("Prueba de convergencia del estimador — tipo:", TIPO)
    print(f"Estimación del usuario: {ESTIMACION_USUARIO} min  "
          f"(constante, el usuario no cambia de opinión)")
    print(f"Duración real media:    {DURACION_REAL_MEDIA} min  "
          f"(+/- {RUIDO} min de ruido)")
    print("=" * 74)

    # Predicción ANTES de tener datos: debe ser exactamente la del usuario (US-A3).
    p0 = est.predecir(TIPO, ESTIMACION_USUARIO)
    print(f"\nn=0  (sin datos)   prediccion = {p0.duracion_esperada_min:6.1f} min   "
          f"w={p0.peso_historia:.2f}   confianza={p0.confianza}")
    assert abs(p0.duracion_esperada_min - ESTIMACION_USUARIO) < 1e-9, \
        "Con 0 observaciones la predicción debe ser la estimación del usuario"

    print("\nObservación |  real  | media aprendida | predicción | incert. | w   | conf.")
    print("-" * 74)

    suma_real = 0
    for i in range(1, N_OBSERVACIONES + 1):
        d = duracion_simulada()
        suma_real += d
        est.actualizar(TIPO, d)

        p = est.predecir(TIPO, ESTIMACION_USUARIO)
        estado = est._tipos[TIPO]
        print(f"    {i:2d}      | {d:4d}   |     {estado.media:6.1f}      |"
              f"   {p.duracion_esperada_min:6.1f}   |  {p.incertidumbre_min:5.1f}  |"
              f" {p.peso_historia:.2f} | {p.confianza}")

    promedio_real = suma_real / N_OBSERVACIONES
    pred_final = est.predecir(TIPO, ESTIMACION_USUARIO)

    print("-" * 74)
    print(f"\nPromedio real de las {N_OBSERVACIONES} observaciones: {promedio_real:.1f} min")
    print(f"Predicción final del estimador:          {pred_final.duracion_esperada_min:.1f} min")
    print(f"Media aprendida:                         {est._tipos[TIPO].media:.1f} min")
    print(f"Peso de la historia (w):                 {pred_final.peso_historia:.2f}")

    # --- Comprobaciones automáticas (US-A2 / US-A3) ---
    error_estimador = abs(pred_final.duracion_esperada_min - promedio_real)
    error_usuario = abs(ESTIMACION_USUARIO - promedio_real)

    print("\nComparación contra la línea base (estimación cruda del usuario):")
    print(f"  Error del usuario (sin aprender): {error_usuario:6.1f} min")
    print(f"  Error del estimador (aprendiendo): {error_estimador:6.1f} min")

    assert pred_final.duracion_esperada_min > ESTIMACION_USUARIO + 20, \
        "Tras aprender, la predicción debe haberse alejado de la estimación cruda"
    assert error_estimador < error_usuario, \
        "El estimador debe quedar MÁS cerca del promedio real que el usuario"

    print("\n✓ Todas las comprobaciones pasaron: el estimador aprende y mejora la")
    print("  predicción frente a usar la estimación cruda del usuario.")


if __name__ == "__main__":
    main()
