# Evaluación del planificador

Generado con `python evaluacion_planificador.py --guardar`. Metodología y lectura de los resultados en [`planificador.md`](planificador.md#5-evidencia-contra-la-línea-base).

| Método | Atraso efectivo medio (min ponderados) | Tareas a tiempo | Tareas agendadas | Prioridad que quedó fuera (media) | Tiempo p95 (s) |
|---|---:|---:|---:|---:|---:|
| EDF | 16,339 | 39.4 % | 79.4 % | 4.16 | 0.00 |
| Recocido | 12,695 | 49.8 % | 84.5 % | 2.25 | 0.17 |
| CP-SAT | 12,666 | 49.6 % | 84.7 % | 2.22 | 2.39 |
| Cota inferior (relajación) | 2,838 | — | — | — | — |

- 150 escenarios (semillas 50–199), de 2 a 25 tareas cada uno.
- CP-SAT frente a EDF: mejor en 124, igual en 26, peor en 0 (primero la prioridad que queda fuera, luego el atraso). En los 55 escenarios donde ambos dejan fuera la misma prioridad, CP-SAT tiene 19 % menos atraso efectivo.
- CP-SAT frente a Recocido: mejor en 19, igual en 131, peor en 0 (primero la prioridad que queda fuera, luego el atraso). En los 146 escenarios donde ambos dejan fuera la misma prioridad, CP-SAT tiene 2 % menos atraso efectivo.
- De la distancia entre EDF y la cota inferior, CP-SAT cierra el 27 %.
- CP-SAT demostró optimalidad en 141 de 150 escenarios (94 %) dentro del límite de 5 s; en el resto entrega la mejor agenda encontrada.
