"""
FocusFlow — Estimador adaptativo de duración de tareas (módulo de Alvaro).

Historias que implementa este archivo:
    US-A1  Mantener media e incertidumbre por tipo de tarea.
    US-A2  Actualizar la estimación de un tipo cada vez que termina una tarea.
    US-A3  Ponderar la estimación del usuario frente a la historia aprendida
           según cuántos datos hay.
    US-A7  Derivar un nivel de confianza (alta / media / baja) de la incertidumbre.

Idea central
------------
Por cada *tipo* de tarea (programacion, lectura, taller, estudio...) el estimador
guarda una creencia sobre cuánto dura ese tipo: una media `mu`, una varianza
(incertidumbre) y cuántas observaciones la respaldan `n`. Cuando el usuario pide
una predicción, el estimador NO devuelve solo `mu`: mezcla la estimación cruda del
usuario con lo aprendido, dándole más peso a la historia a medida que acumula datos.

    d_hat = w * mu + (1 - w) * e        con    w = n / (n + k)

donde `e` es la estimación del usuario, `mu` la media aprendida del tipo, `n` el
número de observaciones de ese tipo y `k` una constante de suavizado (k = 5).

    - Con n = 0  -> w = 0  -> la predicción es exactamente lo que dijo el usuario.
    - Con n grande -> w -> 1 -> la predicción se acerca a la media real aprendida.

Esto es inferencia bayesiana en espíritu: arrancamos con el prior del usuario y lo
vamos corrigiendo con evidencia, sin sobre-confiar en pocos registros.

No usa base de datos ni API: el estado vive en un diccionario en memoria. La
persistencia la añade el backend (módulo de Chacón) más adelante.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Literal

Confianza = Literal["alta", "media", "baja"]

# Constante de suavizado de la ponderación (US-A3).
# Interpretación: cuántas observaciones hacen falta para que la historia aprendida
# y la estimación del usuario pesen igual (w = 0.5 cuando n == K_SUAVIZADO).
K_SUAVIZADO = 5


@dataclass
class EstadoTipo:
    """Creencia acumulada sobre la duración de UN tipo de tarea.

    Mantiene media y varianza de forma *incremental* con el método de Welford,
    sin guardar el historial completo en memoria.

    Atributos:
        n: número de observaciones (duraciones reales) vistas de este tipo.
        media: duración media aprendida, en minutos.
        _m2: suma de cuadrados de las diferencias con la media (acumulador de Welford).
             No se usa directamente; de aquí sale la varianza.
    """

    n: int = 0
    media: float = 0.0
    _m2: float = field(default=0.0, repr=False)

    def observar(self, duracion_real: float) -> None:
        """Incorpora una nueva duración real (US-A2) con el método de Welford.

            n   <- n + 1
            mu  <- mu + (d - mu) / n
            M2  <- M2 + (d - mu_viejo) * (d - mu_nuevo)

        Una sola pasada: actualiza media y varianza sin recorrer el historial.
        """
        self.n += 1
        delta = duracion_real - self.media
        self.media += delta / self.n
        delta2 = duracion_real - self.media
        self._m2 += delta * delta2

    @property
    def varianza(self) -> float:
        """Varianza muestral de las duraciones observadas (incertidumbre bruta).

        Con menos de 2 observaciones no hay varianza definida: devolvemos 0.0.
        """
        if self.n < 2:
            return 0.0
        return self._m2 / (self.n - 1)

    @property
    def desviacion(self) -> float:
        """Desviación estándar en minutos: la incertidumbre en las mismas unidades
        que la duración. Es lo que reportamos como `incertidumbre_min`."""
        return math.sqrt(self.varianza)


@dataclass
class Prediccion:
    """Resultado de una predicción, con todo lo que el contrato de API necesita.

    Campos:
        duracion_esperada_min: la predicción `d_hat`, mezcla usuario/historia.
        incertidumbre_min: desviación estándar aprendida del tipo, en minutos.
        confianza: "alta" / "media" / "baja", derivada de la incertidumbre (US-A7).
        n_observaciones: cuántos datos respaldan la predicción.
        peso_historia: el `w` usado en la mezcla (0 = solo usuario, 1 = solo historia).
                       Útil para explicar y depurar; no forma parte del contrato.
    """

    duracion_esperada_min: float
    incertidumbre_min: float
    confianza: Confianza
    n_observaciones: int
    peso_historia: float


class Estimador:
    """Estimador adaptativo de duraciones, con estado por tipo de tarea.

    Uso típico:
        est = Estimador()
        est.actualizar("programacion", 180)      # el usuario terminó una tarea
        pred = est.predecir("programacion", 90)   # cuánto durará la próxima
        print(pred.duracion_esperada_min, pred.confianza)
    """

    def __init__(self, k: int = K_SUAVIZADO) -> None:
        # k controla qué tan rápido la historia desplaza a la estimación del usuario.
        self.k = k
        self._tipos: Dict[str, EstadoTipo] = {}

    # ---------- US-A1: estructura por tipo ----------

    def _estado(self, tipo: str) -> EstadoTipo:
        """Devuelve (creándolo si hace falta) el estado de un tipo de tarea."""
        if tipo not in self._tipos:
            self._tipos[tipo] = EstadoTipo()
        return self._tipos[tipo]

    def tipos_conocidos(self) -> list[str]:
        """Lista de tipos de tarea con al menos una observación registrada."""
        return [t for t, e in self._tipos.items() if e.n > 0]

    # ---------- US-A2: actualización con duración real ----------

    def actualizar(self, tipo: str, duracion_real: float) -> EstadoTipo:
        """Registra una duración real para un tipo de tarea y devuelve su estado
        actualizado. Aquí es donde el estimador "aprende"."""
        if duracion_real <= 0:
            raise ValueError("duracion_real debe ser mayor que 0")
        estado = self._estado(tipo)
        estado.observar(duracion_real)
        return estado

    # ---------- US-A3 + US-A7: predicción ponderada con confianza ----------

    def _peso_historia(self, n: int) -> float:
        """w = n / (n + k). Crece de 0 hacia 1 a medida que llegan observaciones."""
        return n / (n + self.k)

    def _nivel_confianza(self, estado: EstadoTipo) -> Confianza:
        """Traduce la incertidumbre a una etiqueta legible (US-A7).

        Se combinan dos señales:
          - Pocos datos  -> poca confianza (aunque la varianza salga baja por azar).
          - Mucha dispersión relativa a la media -> poca confianza.

        Se exige un mínimo de datos ANTES de permitir confianza alta: aunque la
        varianza salga baja por azar con 3 registros, no "sabemos" todavía el tipo.

        Umbrales elegidos para que sean explicables ante el jurado, no mágicos
        (evaluados en este orden):
          - n < 3 observaciones              -> "baja" (casi no sabemos nada del tipo).
          - coef. de variación > 0.5         -> "baja" (la duración varía demasiado).
          - coef. de variación > 0.25, o n < 8 -> "media" (datos aún escasos).
          - resto (n >= 8 y poca dispersión) -> "alta".

        El coeficiente de variación (desviación / media) es adimensional, así que
        compara tipos con duraciones muy distintas de forma justa.
        """
        if estado.n < 3 or estado.media <= 0:
            return "baja"
        coef_variacion = estado.desviacion / estado.media
        if coef_variacion > 0.5:
            return "baja"
        if coef_variacion > 0.25 or estado.n < 8:
            return "media"
        return "alta"

    def predecir(self, tipo: str, estimacion_usuario: float) -> Prediccion:
        """Predice cuánto durará una tarea nueva de `tipo`, mezclando la estimación
        del usuario con la historia aprendida (US-A3)."""
        if estimacion_usuario <= 0:
            raise ValueError("estimacion_usuario debe ser mayor que 0")

        estado = self._tipos.get(tipo, EstadoTipo())
        w = self._peso_historia(estado.n)
        duracion_esperada = w * estado.media + (1 - w) * estimacion_usuario

        return Prediccion(
            duracion_esperada_min=duracion_esperada,
            incertidumbre_min=estado.desviacion,
            confianza=self._nivel_confianza(estado),
            n_observaciones=estado.n,
            peso_historia=w,
        )


# =====================================================================
# Adaptador al contrato de API (US-C1)
# ---------------------------------------------------------------------
# El backend (módulo de Chacón) no habla con la clase Estimador directamente:
# habla con el contrato de `schemas.py`, donde todos los minutos son ENTEROS.
# Estas dos funciones traducen el estado interno (floats precisos) a los
# objetos Pydantic exactos que esperan `/estimar` y `/estimador/actualizar`,
# redondeando a int como exige el contrato.
#
# Cómo conectar esto en main.py (reemplaza los mocks):
#
#     from estimador import Estimador, estimar_contrato, actualizar_contrato
#     estimador = Estimador()   # estado global en memoria (o persistido)
#
#     @app.post("/estimar", response_model=schemas.EstimarResponse)
#     def estimar(payload: schemas.EstimarRequest):
#         return estimar_contrato(estimador, payload)
#
#     @app.post("/estimador/actualizar", response_model=schemas.ActualizarEstimadorResponse)
#     def actualizar_estimador(payload: schemas.ActualizarEstimadorRequest):
#         return actualizar_contrato(estimador, payload)
#
# `schemas` se importa de forma perezosa (dentro de las funciones) para que
# `estimador.py` siga ejecutándose solo, sin FastAPI/Pydantic instalados.
# =====================================================================


def estimar_contrato(estimador: "Estimador", payload):
    """Adapta `Estimador.predecir` a un `schemas.EstimarResponse` (enteros).

    Args:
        estimador: la instancia con el estado aprendido.
        payload: un `schemas.EstimarRequest` (tipo, estimacion_usuario_min).

    Returns:
        schemas.EstimarResponse con los minutos redondeados a int.
    """
    import schemas  # import perezoso: el núcleo no depende de Pydantic

    pred = estimador.predecir(payload.tipo, payload.estimacion_usuario_min)
    return schemas.EstimarResponse(
        duracion_esperada_min=round(pred.duracion_esperada_min),
        incertidumbre_min=round(pred.incertidumbre_min),
        confianza=pred.confianza,
        n_observaciones=pred.n_observaciones,
    )


def actualizar_contrato(estimador: "Estimador", payload):
    """Adapta `Estimador.actualizar` a un `schemas.ActualizarEstimadorResponse`.

    Args:
        estimador: la instancia con el estado aprendido.
        payload: un `schemas.ActualizarEstimadorRequest` (tipo, duracion_real_min).

    Returns:
        schemas.ActualizarEstimadorResponse con los minutos redondeados a int.
    """
    import schemas  # import perezoso: el núcleo no depende de Pydantic

    estado = estimador.actualizar(payload.tipo, payload.duracion_real_min)
    return schemas.ActualizarEstimadorResponse(
        media_min=round(estado.media),
        incertidumbre_min=round(estado.desviacion),
        n_observaciones=estado.n,
    )
