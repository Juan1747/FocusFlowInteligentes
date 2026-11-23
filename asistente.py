"""
FocusFlow — Asistente en lenguaje natural (módulo de Cárdenas).

Dos funciones sobre el mismo núcleo del agente:

    POST /interpretar   "Tengo el taller de cálculo para el jueves, 2 horas"
                        -> tarea estructurada (nombre, tipo, límite, prioridad,
                           estimación) que el usuario revisa antes de guardar.
    POST /asistente     chat con herramientas: consulta tareas, tiempo libre,
                        agenda e historial, y PROPONE cambios que el usuario
                        confirma en la interfaz.

Principio de diseño: el modelo de lenguaje propone, el sistema decide.
    - El LLM traduce lenguaje <-> datos. Nunca decide horas ni duraciones: las
      horas salen del planificador (CSP) y las duraciones del estimador.
    - Todo lo que el LLM produce pasa por validación (esquemas de la API) y los
      cambios necesitan la confirmación del usuario.
    - Sin LLM la app sigue funcionando: /interpretar usa un intérprete por
      reglas (que además es la línea base de la evaluación) y la interfaz
      conserva el formulario.

Proveedores (variable de entorno FOCUSFLOW_LLM = auto | claude | gemini | ollama | reglas):
    claude  Claude Haiku 5.5 para interpretar y Claude Opus 5.5 para el chat.
            Necesita ANTHROPIC_API_KEY (o un perfil de `ant auth login`).
    gemini  Gemini Flash-Lite para interpretar y Gemini Flash para el chat, con
            el plan gratuito de Google AI Studio. Necesita GEMINI_API_KEY.
    ollama  modelo local y gratuito (por defecto qwen2.5:3b) en Ollama.
    reglas  sin modelo de lenguaje (solo /interpretar).
    auto    el primero disponible en ese orden: Claude, Gemini, Ollama, reglas.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time as reloj
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db

logger = logging.getLogger("focusflow.asistente")

TIPOS = ("programacion", "lectura", "taller", "estudio_parcial")
ETIQUETA_TIPO = {
    "programacion": "programación",
    "lectura": "lectura",
    "taller": "taller",
    "estudio_parcial": "estudio para parcial",
}
ETIQUETA_PRIORIDAD = {1: "baja", 2: "media", 3: "alta"}
HORA_POR_DEFECTO = time(23, 59)
ESTIMACION_MIN, ESTIMACION_MAX = 5, 24 * 60

MODELO_CLAUDE_INTERPRETAR = "claude-haiku-5-5"
MODELO_CLAUDE_CHAT = "claude-opus-5-5"
# Alias "-latest" de Google: apuntan a la versión vigente de cada familia.
MODELO_GEMINI_INTERPRETAR = os.environ.get("FOCUSFLOW_GEMINI_MODELO_INTERPRETAR", "gemini-flash-lite-latest")
MODELO_GEMINI_CHAT = os.environ.get("FOCUSFLOW_GEMINI_MODELO_CHAT", "gemini-flash-latest")
URL_GEMINI = "https://generativelanguage.googleapis.com/v1beta"
MODELO_OLLAMA = os.environ.get("FOCUSFLOW_OLLAMA_MODELO", "qwen2.5:3b")
URL_OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
MAX_PASOS_AGENTE = 8          # rondas de herramientas por mensaje del usuario
MAX_MENSAJES_HISTORIAL = 20   # turnos de chat que se reenvían al modelo

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
DIAS_CORTOS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


# =====================================================================
# Utilidades de texto y fechas
# =====================================================================

def sin_tildes(texto: str) -> str:
    """Minúsculas sin tildes, conservando la longitud (índice i <-> índice i)."""
    return "".join(unicodedata.normalize("NFD", c)[0] for c in texto.lower())


def fmt_fecha_hora(momento: datetime) -> str:
    return f"{DIAS_CORTOS[momento.weekday()]} {momento.day} {MESES_CORTOS[momento.month - 1]}, {momento:%H:%M}"


def fmt_duracion(minutos: int) -> str:
    h, m = divmod(int(minutos), 60)
    if h and m:
        return f"{h} h {m} min"
    return f"{h} h" if h else f"{m} min"


def calendario(ahora: datetime, dias: int = 21) -> str:
    """Tabla de los próximos días: el LLM la usa para no calcular días de la semana."""
    lineas = []
    for i in range(dias):
        d = ahora.date() + timedelta(days=i)
        marca = " (hoy)" if i == 0 else " (mañana)" if i == 1 else ""
        lineas.append(f"- {DIAS[d.weekday()]} {d.isoformat()}{marca}")
    return "\n".join(lineas)


def _como_entero(valor: Any) -> Optional[int]:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        return int(round(float(valor)))
    except (TypeError, ValueError):
        return None


def _como_fecha(valor: Any) -> Optional[date]:
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor).strip()[:10])
    except ValueError:
        return None


def _como_hora(valor: Any) -> Optional[time]:
    if not valor:
        return None
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*", str(valor))
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        return None
    return time(int(m[1]), int(m[2]))


# =====================================================================
# Parte 1 — Interpretar una frase como tarea
# =====================================================================

def _nulable(esquema: dict) -> dict:
    return {"anyOf": [esquema, {"type": "null"}]}


# Mismo esquema para Claude (salida estructurada) y Ollama (`format`).
ESQUEMA_INTERPRETACION = {
    "type": "object",
    "properties": {
        "nombre": {"type": "string", "description": "Nombre corto de la tarea, sin fecha ni duración."},
        "tipo": {"type": "string", "enum": list(TIPOS)},
        "fecha_limite": _nulable({"type": "string", "description": "AAAA-MM-DD"}),
        "hora_limite": _nulable({"type": "string", "description": "HH:MM en 24 h"}),
        "prioridad": _nulable({"type": "integer", "enum": [1, 2, 3]}),
        "estimacion_min": _nulable({"type": "integer"}),
    },
    "required": ["nombre", "tipo", "fecha_limite", "hora_limite", "prioridad", "estimacion_min"],
    "additionalProperties": False,
}


def sistema_interpretar(ahora: datetime) -> str:
    return f"""Eres el intérprete de tareas de FocusFlow, una app de planificación para estudiantes universitarios en Colombia.
Convierte la frase del usuario en UNA tarea con estos campos:

- nombre: corto y claro (máximo 8 palabras), en español, sin la fecha ni la duración. Ejemplo: "Taller de Cálculo".
- tipo: exactamente uno de
    programacion    escribir, depurar o desplegar código; proyectos de software
    lectura         leer capítulos, artículos, libros, documentación o diapositivas
    taller          ejercicios, guías, talleres, informes y entregables escritos
    estudio_parcial estudiar o repasar para un parcial, examen, final o quiz
- fecha_limite: AAAA-MM-DD, tomada del calendario de abajo. Si nombra un día de la semana igual a hoy,
  es el de la semana siguiente. "La próxima semana" es el mismo día de la semana, 7 días después.
  "En N días" suma N días a hoy. null si no dice fecha.
- hora_limite: HH:MM en 24 horas, solo si la dice ("a las 6 pm" = 18:00, "a las 3" sin am/pm = 15:00,
  "12 del mediodía" = 12:00). null si no dice hora.
- prioridad: 3 si dice que es urgente, importante, crucial o de prioridad alta; 1 si dice que no es urgente,
  no es tan importante, de baja prioridad o "cuando pueda"; 2 si dice prioridad media. null si no dice nada.
- estimacion_min: cuánto cree que le toma, en minutos, solo si lo dice ("hora y media" = 90, "2h" = 120).
  null si no lo dice. Nunca inventes una duración.

Hoy es {DIAS[ahora.weekday()]} {ahora:%Y-%m-%d}, son las {ahora:%H:%M}.
Calendario:
{calendario(ahora)}"""


class InterpretarRequest(BaseModel):
    texto: str = Field(..., min_length=3, max_length=500,
                       examples=["Tengo el taller de cálculo para el jueves, me toma 2 horas"])


class TareaInterpretada(BaseModel):
    nombre: str
    tipo: str
    limite: Optional[datetime] = None
    prioridad: int = 2
    estimacion_usuario_min: Optional[int] = None


class InterpretarResponse(BaseModel):
    tarea: TareaInterpretada
    faltantes: List[str] = Field(default_factory=list,
                                 description="Campos que el usuario debe completar: nombre, limite, estimacion_usuario_min")
    fuente: str = Field(..., description="claude, ollama o reglas")
    modelo: Optional[str] = None
    aviso: Optional[str] = None


def normalizar_interpretacion(crudo: Dict[str, Any], ahora: datetime) -> Tuple[TareaInterpretada, List[str]]:
    """Valida lo que propone el LLM (o las reglas) y lo deja listo para el formulario.

    Nada de lo que sale del modelo se usa sin pasar por aquí: tipo dentro del
    catálogo, fecha real y no pasada, prioridad 1-3, duración razonable.
    """
    faltantes: List[str] = []

    nombre = re.sub(r"\s+", " ", str(crudo.get("nombre") or "")).strip(" .,;:")[:120]
    if nombre:
        nombre = nombre[0].upper() + nombre[1:]
    else:
        faltantes.append("nombre")

    tipo = str(crudo.get("tipo") or "").strip()
    if tipo not in TIPOS:
        tipo = tipo_por_reglas(sin_tildes(nombre)) if nombre else "taller"

    limite = None
    fecha = _como_fecha(crudo.get("fecha_limite"))
    if fecha and ahora.date() <= fecha <= ahora.date() + timedelta(days=366):
        limite = datetime.combine(fecha, _como_hora(crudo.get("hora_limite")) or HORA_POR_DEFECTO)
    else:
        faltantes.append("limite")

    prioridad = _como_entero(crudo.get("prioridad"))
    if prioridad not in (1, 2, 3):
        prioridad = 2

    estimacion = _como_entero(crudo.get("estimacion_min"))
    if estimacion is None or not ESTIMACION_MIN <= estimacion <= ESTIMACION_MAX:
        estimacion = None
        faltantes.append("estimacion_usuario_min")

    return TareaInterpretada(
        nombre=nombre, tipo=tipo, limite=limite, prioridad=prioridad,
        estimacion_usuario_min=estimacion,
    ), faltantes


# ---------- Intérprete por reglas (respaldo sin LLM y línea base) ----------

_NUMEROS = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
            "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10}
_NUM = r"(\d+(?:[.,]\d+)?|" + "|".join(_NUMEROS) + r")"
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]
_DIAS_ST = [sin_tildes(d) for d in DIAS]
_RE_DIA = "|".join(_DIAS_ST)

_PALABRAS_TIPO = {
    "estudio_parcial": [r"\bparcial", r"\bexamen", r"\bquiz", r"\bfinal\b", r"\bestudiar", r"\brepasar"],
    "lectura": [r"\bleer", r"\blectura", r"\bcapitulos?\b", r"\barticulos?\b", r"\bpaper", r"\blibro",
                r"\bnovela", r"\bdiapositivas", r"\bdocumentacion", r"\bpaginas"],
    "programacion": [r"\bprogram", r"\bcodigo", r"\bcodificar", r"\bimplementar", r"\bdebug", r"\bdesarrollar",
                     r"\bapp\b", r"\bapi\b", r"\bbackend", r"\bfrontend", r"\bscript", r"\bbugs?\b",
                     r"\bproyecto", r"\bmontar la base", r"\bmodulo"],
    "taller": [r"\btaller", r"\bejercicios", r"\bguia\b", r"\bproblemas", r"\binforme", r"\blaboratorio",
               r"\btarea\b"],
}


def _numero(texto: str) -> float:
    return float(_NUMEROS.get(texto, None) or texto.replace(",", "."))


def tipo_por_reglas(t: str) -> str:
    """Tipo con más palabras clave; empates en el orden estudio > lectura > programación > taller."""
    puntajes = {tipo: sum(bool(re.search(p, t)) for p in patrones) for tipo, patrones in _PALABRAS_TIPO.items()}
    mejor = max(puntajes.values())
    if mejor == 0:
        return "taller"
    return next(tipo for tipo in ("estudio_parcial", "lectura", "programacion", "taller") if puntajes[tipo] == mejor)


def _fecha_valida(hoy: date, dia: int, mes: int) -> Optional[date]:
    for anio in (hoy.year, hoy.year + 1):
        try:
            candidata = date(anio, mes, dia)
        except ValueError:
            return None
        if candidata >= hoy:
            return candidata
    return None


def _fecha_por_reglas(t: str, hoy: date) -> Optional[date]:
    t = t.replace("de la manana", "")  # "a las 8 de la mañana" no es "mañana"
    if m := re.search(r"\b(\d{1,2}) de (" + "|".join(_MESES) + r")\b", t):
        return _fecha_valida(hoy, int(m[1]), _MESES.index(m[2]) + 1)
    if m := re.search(r"\b(\d{1,2})/(\d{1,2})\b", t):
        return _fecha_valida(hoy, int(m[1]), int(m[2]))
    if re.search(r"\bpasado manana\b", t):
        return hoy + timedelta(days=2)
    if re.search(r"\bmanana\b", t):
        return hoy + timedelta(days=1)
    if re.search(r"\bhoy\b", t):
        return hoy
    if m := re.search(r"\ben " + _NUM + r" dias?\b", t):
        return hoy + timedelta(days=int(_numero(m[1])))
    if re.search(r"\b(proxima|siguiente|otra) semana\b", t):
        return hoy + timedelta(days=7)
    if m := re.search(r"\b(" + _RE_DIA + r")\b", t):
        return hoy + timedelta(days=(_DIAS_ST.index(m[1]) - hoy.weekday()) % 7 or 7)
    if m := re.search(r"\bel (\d{1,2})\b(?! de|/)", t):
        dia = int(m[1])
        mes = hoy.month if dia >= hoy.day else hoy.month % 12 + 1
        return _fecha_valida(hoy, dia, mes)
    return None


_RE_AMPM = r"(?:(a\.?\s?m\.?|p\.?\s?m\.?)(?=\W|$))"


def _hora_por_reglas(t: str) -> Optional[time]:
    m = re.search(
        r"\b(?:a las|antes de las|hasta las)\s+(\d{1,2})(?::(\d{2}))?\s*" + _RE_AMPM + r"?"
        r"(\s*de la (?:manana|tarde|noche|madrugada)|\s*del mediodia)?", t)
    if not m:
        m = re.search(r"\b(\d{1,2}):(\d{2})\s*" + _RE_AMPM + r"?()", t)
    if not m:
        return None
    hora, minuto = int(m[1]), int(m[2] or 0)
    marca = (m[3] or "").replace(".", "").replace(" ", "")
    franja = m[4] or ""
    if "mediodia" in franja:
        hora = 12
    elif marca == "pm" or "tarde" in franja or "noche" in franja:
        hora += 12 if hora < 12 else 0
    elif marca == "am" or "manana" in franja or "madrugada" in franja:
        hora = 0 if hora == 12 else hora
    elif 1 <= hora <= 6:
        hora += 12  # "a las 3" sin am/pm: por la tarde
    if hora > 23 or minuto > 59:
        return None
    return time(hora, minuto)


def _estimacion_por_reglas(t: str) -> Optional[int]:
    if m := re.search(r"\b(\d+)\s*h\s*(\d{1,2})\b", t):  # 1h30
        return int(m[1]) * 60 + int(m[2])
    minutos = 0
    if m := re.search(r"\b" + _NUM + r"\s*(?:h|hrs?|horas?)\b(\s*y\s*media)?", t):
        minutos = round(_numero(m[1]) * 60) + (30 if m[2] else 0)
    elif re.search(r"(?<!\w)(?:una )?hora y media\b", t):
        minutos = 90
    elif re.search(r"\bmedia hora\b", t):
        minutos = 30
    if m := re.search(r"\b(\d+)\s*(?:min|mins|minutos?)\b", t):
        minutos += int(m[1])
    return minutos or None


def _prioridad_por_reglas(t: str) -> Optional[int]:
    if re.search(r"no es (tan )?(urgente|importante)|no urgente|cuando pueda|baja prioridad|prioridad baja|sin afan", t):
        return 1
    if re.search(r"urgente|importantisim|crucial|prioridad alta|alta prioridad|\bimportante\b", t):
        return 3
    if re.search(r"prioridad media", t):
        return 2
    return None


_RE_FECHA_SUELTA = r"(?:hoy|manana|pasado manana|" + _RE_DIA + r"|\d|proxima|siguiente|otra)"
_RE_CORTE_NOMBRE = re.compile(
    r",|\b(?:para|antes de|antes del|hasta)\s+(?:el\s+|la\s+|las\s+)?" + _RE_FECHA_SUELTA +
    r"|\b(?:el|del)\s+(?:" + _RE_DIA + r")\b|\b(?:" + _RE_DIA + r")\b|\bhoy\b|\bpasado manana\b|\bmanana\b"
    r"|\ben \d+ dias?\b|\ba las\b|\bel \d|\bcuando pueda\b"
)


def _nombre_por_reglas(texto: str) -> str:
    t = sin_tildes(texto)
    inicio = 0
    if m := re.match(r"\s*(?:me toca|tengo que|debo|necesito|hay que|toca)\s+", t):
        inicio = m.end()
    corte = _RE_CORTE_NOMBRE.search(t, inicio)
    return texto[inicio: corte.start() if corte else len(texto)].strip(" .,;:")


def interpretar_por_reglas(texto: str, ahora: datetime) -> Dict[str, Any]:
    """Intérprete sin LLM: palabras clave y expresiones de fecha. Devuelve la
    misma forma que el LLM para pasar por la misma validación."""
    t = sin_tildes(texto)
    fecha = _fecha_por_reglas(t, ahora.date())
    hora = _hora_por_reglas(t)
    return {
        "nombre": _nombre_por_reglas(texto),
        "tipo": tipo_por_reglas(t),
        "fecha_limite": fecha.isoformat() if fecha else None,
        "hora_limite": hora.strftime("%H:%M") if hora else None,
        "prioridad": _prioridad_por_reglas(t),
        "estimacion_min": _estimacion_por_reglas(t),
    }


# =====================================================================
# Parte 2 — Proveedores de modelo de lenguaje
# =====================================================================

class ErrorLLM(Exception):
    """El modelo no respondió o respondió algo inutilizable."""


@dataclass
class LlamadaHerramienta:
    id: str
    nombre: str
    argumentos: Dict[str, Any]


@dataclass
class Turno:
    texto: str
    llamadas: List[LlamadaHerramienta] = field(default_factory=list)
    crudo: Any = None  # contenido original del proveedor, para reenviarlo tal cual


@dataclass
class ResultadoHerramienta:
    id: str
    nombre: str
    contenido: str
    es_error: bool = False


# Historial genérico, independiente del proveedor:
#   {"rol": "usuario", "texto": ...}
#   {"rol": "asistente", "texto": ..., "llamadas": [...], "crudo": ...}
#   {"rol": "herramientas", "resultados": [ResultadoHerramienta, ...]}
Historial = List[Dict[str, Any]]


class Proveedor:
    fuente: str = ""

    def modelo(self, uso: Literal["interpretar", "chat"]) -> str:
        raise NotImplementedError

    def json(self, sistema: str, texto: str, esquema: dict) -> Dict[str, Any]:
        raise NotImplementedError

    def paso(self, sistema: str, historial: Historial, herramientas: List["Herramienta"]) -> Turno:
        raise NotImplementedError


class ProveedorClaude(Proveedor):
    """Claude por la API de Anthropic: Haiku 5.5 interpreta, Opus 5.5 conversa."""

    fuente = "claude"

    def __init__(self) -> None:
        import anthropic  # perezoso: sin el paquete, la app funciona con Ollama o reglas

        self._anthropic = anthropic
        self.cliente = anthropic.Anthropic(timeout=90.0, max_retries=2)

    def modelo(self, uso):
        return MODELO_CLAUDE_INTERPRETAR if uso == "interpretar" else MODELO_CLAUDE_CHAT

    def _error(self, e: Exception) -> ErrorLLM:
        a = self._anthropic
        if isinstance(e, a.AuthenticationError):
            return ErrorLLM("La clave de la API de Claude no es válida.")
        if isinstance(e, a.PermissionDeniedError):
            return ErrorLLM("La clave de la API de Claude no tiene permiso para este modelo.")
        if isinstance(e, a.RateLimitError):
            return ErrorLLM("Claude está limitando las solicitudes; intenta en un momento.")
        if isinstance(e, a.APIStatusError):
            return ErrorLLM(f"Claude respondió con un error ({e.status_code}).")
        if isinstance(e, a.APIConnectionError):
            return ErrorLLM("No hay conexión con la API de Claude.")
        return ErrorLLM(str(e))

    def json(self, sistema, texto, esquema):
        try:
            respuesta = self.cliente.messages.create(
                model=MODELO_CLAUDE_INTERPRETAR,
                max_tokens=4000,
                system=sistema,
                messages=[{"role": "user", "content": texto}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": esquema}},
            )
        except self._anthropic.APIError as e:
            raise self._error(e) from e
        if respuesta.stop_reason == "refusal":
            raise ErrorLLM("El modelo se negó a interpretar la frase.")
        if respuesta.stop_reason == "max_tokens":
            raise ErrorLLM("La respuesta del modelo quedó incompleta.")
        texto_json = next((b.text for b in respuesta.content if b.type == "text"), "")
        try:
            return json.loads(texto_json)
        except json.JSONDecodeError as e:
            raise ErrorLLM("El modelo devolvió un JSON inválido.") from e

    def _mensajes(self, historial: Historial) -> List[dict]:
        mensajes = []
        for h in historial:
            if h["rol"] == "usuario":
                mensajes.append({"role": "user", "content": h["texto"]})
            elif h["rol"] == "asistente":
                mensajes.append({"role": "assistant", "content": h.get("crudo") or h["texto"] or "…"})
            else:
                mensajes.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r.id, "content": r.contenido, "is_error": r.es_error}
                    for r in h["resultados"]
                ]})
        return mensajes

    def paso(self, sistema, historial, herramientas):
        try:
            respuesta = self.cliente.beta.messages.create(
                model=MODELO_CLAUDE_CHAT,
                max_tokens=16000,
                system=sistema,
                messages=self._mensajes(historial),
                tools=[{"name": h.nombre, "description": h.descripcion,
                        "input_schema": h.parametros, "strict": True} for h in herramientas],
                output_config={"effort": "medium"},
                # Si un clasificador de seguridad rechaza la solicitud, la API la
                # reintenta sola con el modelo de respaldo adecuado.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except self._anthropic.APIError as e:
            raise self._error(e) from e
        if respuesta.stop_reason == "refusal":
            return Turno("No puedo ayudarte con eso. Pregúntame por tus tareas, tu tiempo libre o tu agenda.")
        if respuesta.stop_reason == "max_tokens":
            raise ErrorLLM("La respuesta del modelo quedó incompleta.")
        texto = "".join(b.text for b in respuesta.content if b.type == "text").strip()
        llamadas = [LlamadaHerramienta(b.id, b.name, dict(b.input))
                    for b in respuesta.content if b.type == "tool_use"]
        return Turno(texto, llamadas, crudo=respuesta.content)


def esquema_gemini(esquema: dict) -> dict:
    """JSON Schema (el de Claude y Ollama) -> el subconjunto OpenAPI que acepta
    Gemini: `anyOf [X, null]` pasa a `nullable`, los tipos van en mayúscula,
    `enum` solo se permite en textos y no existe `additionalProperties`."""
    if "anyOf" in esquema:
        opciones = [o for o in esquema["anyOf"] if o.get("type") != "null"]
        base = esquema_gemini(opciones[0]) if len(opciones) == 1 else {"type": "STRING"}
        if len(opciones) < len(esquema["anyOf"]):
            base["nullable"] = True
        return base
    salida: Dict[str, Any] = {}
    tipo = esquema.get("type")
    if tipo:
        salida["type"] = tipo.upper()
    descripcion = esquema.get("description", "")
    if "enum" in esquema:
        if tipo == "string":
            salida["enum"] = list(esquema["enum"])
        else:  # Gemini no admite enum numérico: va en la descripción y se valida al normalizar
            descripcion = (descripcion + " Uno de: " + ", ".join(map(str, esquema["enum"])) + ".").strip()
    if descripcion:
        salida["description"] = descripcion
    if "properties" in esquema:
        salida["properties"] = {k: esquema_gemini(v) for k, v in esquema["properties"].items()}
        salida["required"] = list(esquema.get("required", []))
        salida["propertyOrdering"] = list(esquema["properties"])
    if "items" in esquema:
        salida["items"] = esquema_gemini(esquema["items"])
    return salida


class ProveedorGemini(Proveedor):
    """Gemini por la API REST de Google AI Studio (sirve el plan gratuito).

    Ojo: en el plan gratuito Google puede usar lo que se envía para mejorar sus
    productos. Se documenta en docs/asistente.md y se dice en la sustentación."""

    fuente = "gemini"

    def __init__(self, clave: Optional[str] = None,
                 modelo_interpretar: str = MODELO_GEMINI_INTERPRETAR,
                 modelo_chat: str = MODELO_GEMINI_CHAT, url: str = URL_GEMINI) -> None:
        self._clave = clave or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self._clave:
            raise ErrorLLM("Falta GEMINI_API_KEY.")
        self._modelos = {"interpretar": modelo_interpretar, "chat": modelo_chat}
        self.url = url

    def modelo(self, uso):
        return self._modelos[uso]

    def _generar(self, uso: str, cuerpo: dict) -> dict:
        import requests

        modelo = self._modelos[uso]
        for intento in range(2):
            try:
                # La clave va en un encabezado, nunca en la URL (así no queda en logs).
                respuesta = requests.post(f"{self.url}/models/{modelo}:generateContent", json=cuerpo,
                                          headers={"x-goog-api-key": self._clave}, timeout=90)
            except requests.RequestException as e:
                raise ErrorLLM("No hay conexión con la API de Gemini.") from e
            if respuesta.status_code == 429 and intento == 0:
                espera = self._espera_sugerida(respuesta)
                if espera is not None and espera <= 20:
                    reloj.sleep(espera)
                    continue
            break
        if respuesta.status_code == 429:
            raise ErrorLLM(f"Se agotó la cuota gratuita de Gemini para {modelo}; intenta más tarde "
                           "o usa un modelo con más cuota (ver docs/asistente.md).")
        if respuesta.status_code in (400, 401, 403) and "API_KEY" in respuesta.text:
            raise ErrorLLM("La clave de la API de Gemini no es válida.")
        if respuesta.status_code == 404:
            raise ErrorLLM(f"Gemini no reconoce el modelo {modelo}.")
        if respuesta.status_code >= 400:
            raise ErrorLLM(f"Gemini respondió con un error ({respuesta.status_code}).")
        try:
            datos = respuesta.json()
        except ValueError as e:
            raise ErrorLLM("Gemini devolvió una respuesta inesperada.") from e
        if (datos.get("promptFeedback") or {}).get("blockReason"):
            raise ErrorLLM("Gemini bloqueó la solicitud.")
        candidatos = datos.get("candidates") or []
        if not candidatos:
            raise ErrorLLM("Gemini no devolvió respuesta.")
        candidato = candidatos[0]
        if candidato.get("finishReason") == "MAX_TOKENS":
            raise ErrorLLM("La respuesta de Gemini quedó incompleta.")
        return candidato.get("content") or {"role": "model", "parts": []}

    @staticmethod
    def _espera_sugerida(respuesta) -> Optional[float]:
        """Segundos que Google pide esperar tras un 429 (campo retryDelay), si los da."""
        try:
            for detalle in respuesta.json().get("error", {}).get("details", []):
                demora = str(detalle.get("retryDelay", ""))
                if demora.endswith("s"):
                    return float(demora[:-1]) + 0.5
        except (ValueError, AttributeError):
            pass
        return None

    def json(self, sistema, texto, esquema):
        contenido = self._generar("interpretar", {
            "systemInstruction": {"parts": [{"text": sistema}]},
            "contents": [{"role": "user", "parts": [{"text": texto}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                 "responseSchema": esquema_gemini(esquema)},
        })
        texto_json = "".join(p.get("text", "") for p in contenido.get("parts", []) if not p.get("thought"))
        try:
            return json.loads(texto_json)
        except json.JSONDecodeError as e:
            raise ErrorLLM("Gemini devolvió un JSON inválido.") from e

    @staticmethod
    def _respuesta_herramienta(r: ResultadoHerramienta) -> dict:
        try:
            valor = json.loads(r.contenido)
        except json.JSONDecodeError:
            valor = r.contenido
        if r.es_error:
            return {"error": valor}
        return valor if isinstance(valor, dict) else {"resultado": valor}

    def paso(self, sistema, historial, herramientas):
        contenidos = []
        for h in historial:
            if h["rol"] == "usuario":
                contenidos.append({"role": "user", "parts": [{"text": h["texto"]}]})
            elif h["rol"] == "asistente":
                # Se reenvía el contenido original: conserva las firmas de
                # razonamiento (thoughtSignature) que Gemini exige en el loop.
                contenidos.append(h.get("crudo") or {"role": "model", "parts": [{"text": h["texto"] or "…"}]})
            else:
                partes = []
                for r in h["resultados"]:
                    respuesta = {"name": r.nombre, "response": self._respuesta_herramienta(r)}
                    if not r.id.startswith("gem-"):  # id que puso Gemini: se devuelve
                        respuesta["id"] = r.id
                    partes.append({"functionResponse": respuesta})
                contenidos.append({"role": "user", "parts": partes})
        contenido = self._generar("chat", {
            "systemInstruction": {"parts": [{"text": sistema}]},
            "contents": contenidos,
            "tools": [{"functionDeclarations": [
                {"name": h.nombre, "description": h.descripcion, "parameters": esquema_gemini(h.parametros)}
                for h in herramientas]}],
            "generationConfig": {"temperature": 0.2},
        })
        partes = contenido.get("parts", [])
        texto = "".join(p.get("text", "") for p in partes if not p.get("thought")).strip()
        llamadas = []
        for p in partes:
            llamada = p.get("functionCall")
            if llamada:
                llamadas.append(LlamadaHerramienta(llamada.get("id") or f"gem-{uuid.uuid4().hex[:10]}",
                                                   llamada.get("name", ""), dict(llamada.get("args") or {})))
        contenido.setdefault("role", "model")
        return Turno(texto, llamadas, crudo=contenido)


class ProveedorOllama(Proveedor):
    """Modelo local y gratuito servido por Ollama (https://ollama.com)."""

    fuente = "ollama"

    def __init__(self, modelo: str = MODELO_OLLAMA, url: str = URL_OLLAMA) -> None:
        self._modelo, self.url = modelo, url

    def modelo(self, uso):
        return self._modelo

    @staticmethod
    def disponible(modelo: str = MODELO_OLLAMA, url: str = URL_OLLAMA) -> bool:
        import requests

        try:
            respuesta = requests.get(f"{url}/api/tags", timeout=0.8)
            respuesta.raise_for_status()
        except requests.RequestException:
            return False
        nombres = {m.get("name", "") for m in respuesta.json().get("models", [])}
        return modelo in nombres or f"{modelo}:latest" in nombres

    def _chat(self, cuerpo: dict) -> dict:
        import requests

        try:
            respuesta = requests.post(f"{self.url}/api/chat", json=cuerpo, timeout=180)
            respuesta.raise_for_status()
            return respuesta.json()["message"]
        except requests.RequestException as e:
            raise ErrorLLM(f"No se pudo usar el modelo local ({self._modelo}): {e}") from e
        except (KeyError, ValueError) as e:
            raise ErrorLLM("El modelo local devolvió una respuesta inesperada.") from e

    def json(self, sistema, texto, esquema):
        mensaje = self._chat({
            "model": self._modelo, "stream": False, "format": esquema,
            "options": {"temperature": 0},
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": texto}],
        })
        try:
            return json.loads(mensaje.get("content", ""))
        except json.JSONDecodeError as e:
            raise ErrorLLM("El modelo local devolvió un JSON inválido.") from e

    def paso(self, sistema, historial, herramientas):
        mensajes: List[dict] = [{"role": "system", "content": sistema}]
        for h in historial:
            if h["rol"] == "usuario":
                mensajes.append({"role": "user", "content": h["texto"]})
            elif h["rol"] == "asistente":
                mensaje = {"role": "assistant", "content": h["texto"] or ""}
                if h.get("llamadas"):
                    mensaje["tool_calls"] = [{"function": {"name": ll.nombre, "arguments": ll.argumentos}}
                                             for ll in h["llamadas"]]
                mensajes.append(mensaje)
            else:
                mensajes.extend({"role": "tool", "tool_name": r.nombre, "content": r.contenido}
                                for r in h["resultados"])
        mensaje = self._chat({
            "model": self._modelo, "stream": False, "options": {"temperature": 0.2},
            "messages": mensajes,
            "tools": [{"type": "function", "function": {"name": h.nombre, "description": h.descripcion,
                                                         "parameters": h.parametros}} for h in herramientas],
        })
        llamadas = []
        for llamada in mensaje.get("tool_calls") or []:
            funcion = llamada.get("function", {})
            argumentos = funcion.get("arguments") or {}
            if isinstance(argumentos, str):
                try:
                    argumentos = json.loads(argumentos)
                except json.JSONDecodeError:
                    argumentos = {}
            llamadas.append(LlamadaHerramienta(uuid.uuid4().hex[:12], funcion.get("name", ""), argumentos))
        return Turno((mensaje.get("content") or "").strip(), llamadas)


_cache_proveedor: Dict[str, Any] = {"momento": 0.0, "valor": None}


def elegir_proveedor() -> Optional[Proveedor]:
    """Proveedor según FOCUSFLOW_LLM. None = sin modelo de lenguaje (reglas)."""
    preferido = os.environ.get("FOCUSFLOW_LLM", "auto").strip().lower()
    if preferido == "reglas":
        return None
    hay_claude = any(os.environ.get(v) for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"))
    if preferido == "claude" or (preferido == "auto" and hay_claude):
        try:
            return ProveedorClaude()
        except ImportError:
            logger.warning("FOCUSFLOW_LLM pide Claude pero falta el paquete: pip install anthropic")
    hay_gemini = any(os.environ.get(v) for v in ("GEMINI_API_KEY", "GOOGLE_API_KEY"))
    if preferido == "gemini" or (preferido == "auto" and hay_gemini):
        try:
            return ProveedorGemini()
        except ErrorLLM as e:
            logger.warning("FOCUSFLOW_LLM pide Gemini: %s", e)
    if preferido in ("auto", "ollama") and ProveedorOllama.disponible():
        return ProveedorOllama()
    return None


def proveedor_actual() -> Optional[Proveedor]:
    """Dependencia de FastAPI. Se recalcula cada 30 s (por si se enciende Ollama)."""
    if reloj.monotonic() - _cache_proveedor["momento"] > 30:
        _cache_proveedor.update(momento=reloj.monotonic(), valor=elegir_proveedor())
    return _cache_proveedor["valor"]


def interpretar(texto: str, ahora: datetime, proveedor: Optional[Proveedor]) -> InterpretarResponse:
    """Frase -> tarea. Con LLM si hay; si falla o no hay, por reglas."""
    aviso = None
    if proveedor is not None:
        try:
            crudo = proveedor.json(sistema_interpretar(ahora), texto, ESQUEMA_INTERPRETACION)
            tarea, faltantes = normalizar_interpretacion(crudo, ahora)
            return InterpretarResponse(tarea=tarea, faltantes=faltantes, fuente=proveedor.fuente,
                                       modelo=proveedor.modelo("interpretar"))
        except ErrorLLM as e:
            logger.warning("interpretar: %s; se usan reglas", e)
            aviso = f"{e} Se usó el intérprete básico; revisa los campos."
    tarea, faltantes = normalizar_interpretacion(interpretar_por_reglas(texto, ahora), ahora)
    return InterpretarResponse(tarea=tarea, faltantes=faltantes, fuente="reglas", aviso=aviso)


# =====================================================================
# Parte 3 — Herramientas del agente
# =====================================================================

class ErrorHerramienta(Exception):
    """Error que se le devuelve al modelo para que corrija la llamada."""


class Propuesta(BaseModel):
    """Cambio que el agente sugiere. La interfaz lo ejecuta con los endpoints
    de siempre solo si el usuario lo confirma."""

    id: str
    accion: Literal["crear_tarea", "editar_tarea", "bloques_dia"]
    resumen: str
    datos: Dict[str, Any]
    tarea_id: Optional[int] = None


@dataclass
class Contexto:
    db: Session
    ahora: datetime
    peticion_plan: Callable[[Session, datetime], schemas.PlanificarRequest]
    planificar: Callable[[schemas.PlanificarRequest], schemas.PlanificarResponse]
    propuestas: List[Propuesta] = field(default_factory=list)
    consultas: List[str] = field(default_factory=list)


@dataclass
class Herramienta:
    nombre: str
    descripcion: str
    parametros: dict
    ejecutar: Callable[[Contexto, Dict[str, Any]], Dict[str, Any]]
    etiqueta: str  # cómo se muestra en la interfaz ("consultó tu agenda")


def _objeto(propiedades: Dict[str, dict]) -> dict:
    """Esquema compatible con `strict: true`: todo requerido, opcionales nulables."""
    return {"type": "object", "properties": propiedades,
            "required": list(propiedades), "additionalProperties": False}


def _tarea_dict(t: models.Tarea) -> dict:
    return {"id": t.id, "nombre": t.nombre, "tipo": t.tipo, "limite": t.limite.isoformat(timespec="minutes"),
            "prioridad": t.prioridad, "estimacion_usuario_min": t.estimacion_usuario_min, "estado": t.estado}


def h_ver_tareas(ctx: Contexto, args: dict) -> dict:
    estado = args.get("estado") or "pendientes"
    consulta = ctx.db.query(models.Tarea)
    if estado == "pendientes":
        consulta = consulta.filter(models.Tarea.estado != "terminada")
    elif estado == "terminadas":
        consulta = consulta.filter(models.Tarea.estado == "terminada")
    tareas = consulta.order_by(models.Tarea.limite).limit(60).all()
    return {"tareas": [_tarea_dict(t) for t in tareas], "total": len(tareas)}


def h_ver_bloques(ctx: Contexto, args: dict) -> dict:
    desde = _como_fecha(args.get("desde")) or ctx.ahora.date()
    hasta = _como_fecha(args.get("hasta")) or desde + timedelta(days=13)
    bloques = (ctx.db.query(models.BloqueLibre)
               .filter(models.BloqueLibre.inicio >= datetime.combine(desde, time.min),
                       models.BloqueLibre.inicio < datetime.combine(hasta + timedelta(days=1), time.min))
               .order_by(models.BloqueLibre.inicio).all())
    dias: Dict[str, dict] = {}
    for b in bloques:
        dia = dias.setdefault(b.inicio.date().isoformat(),
                              {"dia": f"{DIAS[b.inicio.weekday()]} {b.inicio.date().isoformat()}",
                               "bloques": [], "total_min": 0})
        dia["bloques"].append(f"{b.inicio:%H:%M}-{b.fin:%H:%M}")
        dia["total_min"] += round((b.fin - b.inicio).total_seconds() / 60)
    return {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "dias": list(dias.values()),
            "nota": "Los días que no aparecen no tienen tiempo libre declarado."}


def h_ver_agenda(ctx: Contexto, args: dict) -> dict:
    peticion = ctx.peticion_plan(ctx.db, ctx.ahora)
    plan = ctx.planificar(peticion)
    tareas = {t.id: t for t in ctx.db.query(models.Tarea).all()}
    prevista = {t.id: t.duracion_min for t in peticion.tareas}
    agendadas = {a.tarea_id for a in plan.agenda}
    libre = sum((b.fin - b.inicio).total_seconds() / 60 for b in peticion.bloques_libres)
    ocupado = sum(prevista[a.tarea_id] for a in plan.agenda)

    def nombre(tid):
        return tareas[tid].nombre if tid in tareas else f"Tarea #{tid}"

    return {
        "calculada_desde": ctx.ahora.isoformat(timespec="minutes"),
        "todas_caben": plan.factible,
        "agenda": [{
            "tarea_id": a.tarea_id, "nombre": nombre(a.tarea_id),
            "inicio": a.inicio.isoformat(timespec="minutes"), "fin": a.fin.isoformat(timespec="minutes"),
            "duracion_prevista_min": prevista[a.tarea_id],
            "usuario_estimo_min": tareas[a.tarea_id].estimacion_usuario_min if a.tarea_id in tareas else None,
        } for a in plan.agenda],
        "en_riesgo": [{
            "tarea_id": r.tarea_id, "nombre": nombre(r.tarea_id), "holgura_min": r.holgura_min,
            "sin_espacio_en_agenda": r.tarea_id not in agendadas,
        } for r in plan.en_riesgo],
        "tiempo_libre_restante_min": round(libre),
        "tiempo_sin_asignar_min": round(libre - ocupado),
        "nota": ("Las horas las decidió el planificador; las duraciones previstas son del estimador. "
                 "holgura_min = minutos entre el fin y el plazo; negativa = va tarde."),
    }


def h_ver_historial(ctx: Contexto, args: dict) -> dict:
    registros = ctx.db.query(models.Registro).order_by(models.Registro.terminada_en).all()
    por_tipo: Dict[str, dict] = {}
    for r in registros:
        d = por_tipo.setdefault(r.tipo, {"tipo": r.tipo, "n": 0, "estimado": 0, "real": 0, "factores": []})
        d["n"] += 1
        d["estimado"] += r.estimacion_min
        d["real"] += r.duracion_real_min
        d["factores"].append(r.duracion_real_min / max(1, r.estimacion_min))
    resumen = [{
        "tipo": d["tipo"], "tareas_terminadas": d["n"],
        "estimado_medio_min": round(d["estimado"] / d["n"]), "real_medio_min": round(d["real"] / d["n"]),
        "factor_medio": round(sum(d["factores"]) / d["n"], 2),
    } for d in por_tipo.values()]
    return {
        "por_tipo": sorted(resumen, key=lambda x: -x["factor_medio"]),
        "ultimas": [{"tipo": r.tipo, "estimado_min": r.estimacion_min, "real_min": r.duracion_real_min,
                     "terminada": r.terminada_en.isoformat(timespec="minutes")} for r in registros[-5:]],
        "nota": "factor_medio = real / estimado. 1.5 significa que tarda 50 % más de lo que dice.",
    }


def _limite(fecha: Any, hora: Any, base: Optional[datetime], ahora: datetime) -> Optional[datetime]:
    """Combina fecha y hora pedidas con el límite actual (al editar solo una de las dos)."""
    f, h = _como_fecha(fecha), _como_hora(hora)
    if fecha and f is None:
        raise ErrorHerramienta("fecha_limite debe ser AAAA-MM-DD.")
    if hora and h is None:
        raise ErrorHerramienta("hora_limite debe ser HH:MM en 24 horas.")
    if f is None and h is None:
        return None
    if f is None and base is None:
        raise ErrorHerramienta("Falta fecha_limite; pregúntale al usuario para cuándo es.")
    nueva = datetime.combine(f or base.date(), h or (base.time() if base else HORA_POR_DEFECTO))
    if nueva < ahora:
        raise ErrorHerramienta(f"El límite {nueva:%Y-%m-%d %H:%M} ya pasó; pídele al usuario otra fecha.")
    return nueva


def _validar(modelo, datos: dict):
    try:
        return modelo(**datos)
    except ValidationError as e:
        errores = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
        raise ErrorHerramienta(f"Datos inválidos: {errores}") from e


def _nueva_propuesta(ctx: Contexto, **campos) -> dict:
    propuesta = Propuesta(id=uuid.uuid4().hex[:10], **campos)
    ctx.propuestas.append(propuesta)
    return {"propuesta_id": propuesta.id, "resumen": propuesta.resumen,
            "estado": "Pendiente: el usuario la verá como tarjeta y decidirá si la confirma. Aún no se aplicó."}


def h_proponer_crear(ctx: Contexto, args: dict) -> dict:
    tipo = args.get("tipo")
    if tipo not in TIPOS:
        raise ErrorHerramienta(f"tipo debe ser uno de {', '.join(TIPOS)}.")
    limite = _limite(args.get("fecha_limite"), args.get("hora_limite"), None, ctx.ahora)
    if limite is None:
        raise ErrorHerramienta("Falta fecha_limite; pregúntale al usuario para cuándo es.")
    tarea = _validar(schemas.TareaCreate, {
        "nombre": (args.get("nombre") or "").strip(), "tipo": tipo, "limite": limite,
        "prioridad": _como_entero(args.get("prioridad")) or 2,
        "estimacion_usuario_min": _como_entero(args.get("estimacion_min")),
    })
    resumen = (f"Crear «{tarea.nombre}» ({ETIQUETA_TIPO[tarea.tipo]}) · vence {fmt_fecha_hora(tarea.limite)} · "
               f"prioridad {ETIQUETA_PRIORIDAD.get(tarea.prioridad, tarea.prioridad)} · "
               f"tú estimas {fmt_duracion(tarea.estimacion_usuario_min)}")
    return _nueva_propuesta(ctx, accion="crear_tarea", resumen=resumen, datos=tarea.model_dump(mode="json"))


def h_proponer_editar(ctx: Contexto, args: dict) -> dict:
    tarea_id = _como_entero(args.get("tarea_id"))
    tarea = ctx.db.get(models.Tarea, tarea_id) if tarea_id else None
    if tarea is None:
        raise ErrorHerramienta(f"No existe la tarea {args.get('tarea_id')}; usa ver_tareas para ver los id.")
    cambios: Dict[str, Any] = {}
    textos: List[str] = []
    if args.get("nombre"):
        cambios["nombre"] = args["nombre"].strip()
        textos.append(f"nombre «{tarea.nombre}» → «{cambios['nombre']}»")
    limite = _limite(args.get("fecha_limite"), args.get("hora_limite"), tarea.limite, ctx.ahora)
    if limite and limite != tarea.limite:
        cambios["limite"] = limite
        textos.append(f"límite {fmt_fecha_hora(tarea.limite)} → {fmt_fecha_hora(limite)}")
    prioridad = _como_entero(args.get("prioridad"))
    if prioridad and prioridad != tarea.prioridad:
        cambios["prioridad"] = prioridad
        textos.append(f"prioridad {ETIQUETA_PRIORIDAD.get(tarea.prioridad)} → {ETIQUETA_PRIORIDAD.get(prioridad, prioridad)}")
    estimacion = _como_entero(args.get("estimacion_min"))
    if estimacion and estimacion != tarea.estimacion_usuario_min:
        cambios["estimacion_usuario_min"] = estimacion
        textos.append(f"tu estimación {fmt_duracion(tarea.estimacion_usuario_min)} → {fmt_duracion(estimacion)}")
    if not cambios:
        raise ErrorHerramienta("No hay ningún cambio respecto a la tarea actual.")
    validado = _validar(schemas.TareaUpdate, cambios)
    return _nueva_propuesta(
        ctx, accion="editar_tarea", tarea_id=tarea.id,
        resumen=f"Editar «{tarea.nombre}»: " + "; ".join(textos),
        datos=validado.model_dump(mode="json", exclude_unset=True),
    )


def h_proponer_bloque(ctx: Contexto, args: dict) -> dict:
    dia = _como_fecha(args.get("dia"))
    inicio, fin = _como_hora(args.get("inicio")), _como_hora(args.get("fin"))
    if dia is None or inicio is None or fin is None:
        raise ErrorHerramienta("dia debe ser AAAA-MM-DD; inicio y fin, HH:MM en 24 horas.")
    if dia < ctx.ahora.date():
        raise ErrorHerramienta("Ese día ya pasó.")
    existentes = (ctx.db.query(models.BloqueLibre)
                  .filter(models.BloqueLibre.inicio >= datetime.combine(dia, time.min),
                          models.BloqueLibre.inicio < datetime.combine(dia + timedelta(days=1), time.min))
                  .order_by(models.BloqueLibre.inicio).all())
    rangos = [{"inicio": b.inicio.strftime("%H:%M"), "fin": b.fin.strftime("%H:%M")} for b in existentes]
    rangos.append({"inicio": inicio.strftime("%H:%M"), "fin": fin.strftime("%H:%M")})
    validado = _validar(schemas.BloquesDiaCreate, {"dia": dia, "bloques": rangos})
    quedaria = ", ".join(f"{r.inicio:%H:%M}-{r.fin:%H:%M}" for r in sorted(validado.bloques, key=lambda r: r.inicio))
    return _nueva_propuesta(
        ctx, accion="bloques_dia",
        resumen=(f"Agregar tiempo libre el {DIAS[dia.weekday()]} {dia.day} {MESES_CORTOS[dia.month - 1]} "
                 f"de {inicio:%H:%M} a {fin:%H:%M} (ese día quedaría: {quedaria})"),
        datos=validado.model_dump(mode="json"),
    )


_FECHA = _nulable({"type": "string", "description": "AAAA-MM-DD"})
_HORA = _nulable({"type": "string", "description": "HH:MM en 24 horas"})

HERRAMIENTAS: List[Herramienta] = [
    Herramienta(
        "ver_tareas", "Lista las tareas del usuario con id, nombre, tipo, límite, prioridad, su estimación y estado.",
        _objeto({"estado": {"type": "string", "enum": ["pendientes", "terminadas", "todas"]}}),
        h_ver_tareas, "tus tareas"),
    Herramienta(
        "ver_bloques_libres", "Muestra el tiempo libre que el usuario declaró por día. Sin fechas: hoy y 13 días más.",
        _objeto({"desde": _FECHA, "hasta": _FECHA}), h_ver_bloques, "tu tiempo libre"),
    Herramienta(
        "ver_agenda",
        "Calcula la agenda propuesta desde ahora con el planificador (CSP) y las duraciones del estimador: "
        "a qué hora va cada tarea, cuáles están en riesgo o no caben y cuánto tiempo libre sobra. "
        "Úsala antes de hablar de horarios o de si al usuario le alcanza el tiempo.",
        _objeto({}), h_ver_agenda, "tu agenda"),
    Herramienta(
        "ver_historial",
        "Resume las tareas terminadas: por tipo, cuánto estimaba el usuario y cuánto tardó de verdad.",
        _objeto({}), h_ver_historial, "tu historial"),
    Herramienta(
        "proponer_crear_tarea",
        "Propone crear una tarea. NO la crea: el usuario la confirma en la interfaz.",
        _objeto({
            "nombre": {"type": "string"},
            "tipo": {"type": "string", "enum": list(TIPOS)},
            "fecha_limite": {"type": "string", "description": "AAAA-MM-DD"},
            "hora_limite": _HORA,
            "prioridad": {"type": "integer", "enum": [1, 2, 3]},
            "estimacion_min": {"type": "integer", "description": "Cuánto cree el usuario que le toma, en minutos."},
        }), h_proponer_crear, "propuso crear una tarea"),
    Herramienta(
        "proponer_editar_tarea",
        "Propone cambiar nombre, fecha/hora límite, prioridad o estimación de una tarea existente. "
        "Deja en null lo que no cambia. NO aplica el cambio: el usuario lo confirma.",
        _objeto({
            "tarea_id": {"type": "integer"},
            "nombre": _nulable({"type": "string"}),
            "fecha_limite": _FECHA,
            "hora_limite": _HORA,
            "prioridad": _nulable({"type": "integer", "enum": [1, 2, 3]}),
            "estimacion_min": _nulable({"type": "integer"}),
        }), h_proponer_editar, "propuso editar una tarea"),
    Herramienta(
        "proponer_agregar_bloque_libre",
        "Propone agregar un bloque de tiempo libre en un día, conservando los que ya tiene. "
        "NO lo agrega: el usuario lo confirma.",
        _objeto({
            "dia": {"type": "string", "description": "AAAA-MM-DD"},
            "inicio": {"type": "string", "description": "HH:MM en 24 horas"},
            "fin": {"type": "string", "description": "HH:MM en 24 horas"},
        }), h_proponer_bloque, "propuso agregar tiempo libre"),
]
_POR_NOMBRE = {h.nombre: h for h in HERRAMIENTAS}


def ejecutar_herramienta(ctx: Contexto, llamada: LlamadaHerramienta) -> ResultadoHerramienta:
    herramienta = _POR_NOMBRE.get(llamada.nombre)
    if herramienta is None:
        return ResultadoHerramienta(llamada.id, llamada.nombre, f"No existe la herramienta {llamada.nombre}.", True)
    try:
        salida = herramienta.ejecutar(ctx, llamada.argumentos or {})
        if herramienta.etiqueta not in ctx.consultas:
            ctx.consultas.append(herramienta.etiqueta)
        return ResultadoHerramienta(llamada.id, llamada.nombre, json.dumps(salida, ensure_ascii=False))
    except ErrorHerramienta as e:
        return ResultadoHerramienta(llamada.id, llamada.nombre, str(e), True)
    except Exception:  # un fallo interno no debe tumbar la conversación
        logger.exception("herramienta %s", llamada.nombre)
        return ResultadoHerramienta(llamada.id, llamada.nombre, "Error interno al ejecutar la herramienta.", True)


# =====================================================================
# Parte 4 — El agente conversacional
# =====================================================================

def sistema_chat(ahora: datetime) -> str:
    return f"""Eres el asistente de FocusFlow, un agente de planificación para estudiantes universitarios.
FocusFlow aprende cuánto se equivoca el usuario al estimar sus tareas y arma una agenda que sí puede cumplir.
Habla en español de Colombia, cercano y breve (máximo unas 120 palabras salvo que pidan detalle). Usa nombres
de tareas, días y horas concretos, nunca los id internos.

Cómo trabajas:
1. Nunca inventes tareas, horas ni duraciones. Las horas de la agenda las decide el planificador: llama a
   ver_agenda antes de hablar de horarios, de qué hacer hoy o de si le alcanza el tiempo.
2. No puedes cambiar nada directamente. Para crear o editar una tarea o agregar tiempo libre usa las
   herramientas proponer_*; el usuario verá una tarjeta y decidirá. Dile que el cambio queda pendiente de
   su confirmación, no que ya está hecho.
3. No puedes fijar a qué hora se hace una tarea. Si quiere "moverla", explica que el planificador decide las
   horas y ofrece lo que sí cambia la agenda: la prioridad, la fecha límite o agregar tiempo libre.
4. Si falta un dato para proponer algo (por ejemplo, para cuándo es o cuánto cree que le toma), pregúntalo en
   vez de suponerlo.
5. Las duraciones previstas vienen del estimador, que corrige lo que el usuario dice con su historial. Cuando
   sirva, menciónalo ("dijiste 1 h, pero según tu historial reservé 1 h 40").
6. Prioridad: 3 alta, 2 media, 1 baja. Tipos: programacion, lectura, taller, estudio_parcial.
7. Si te piden algo que no tiene que ver con sus tareas o su tiempo, dilo en una frase y vuelve al tema.

Ahora: {DIAS[ahora.weekday()]} {ahora:%Y-%m-%d %H:%M}.
Calendario:
{calendario(ahora, 14)}"""


class MensajeChat(BaseModel):
    rol: Literal["usuario", "asistente"]
    texto: str = Field(..., min_length=1, max_length=4000)


class AsistenteRequest(BaseModel):
    mensajes: List[MensajeChat] = Field(..., min_length=1, max_length=60)


class AsistenteResponse(BaseModel):
    respuesta: str
    propuestas: List[Propuesta] = Field(default_factory=list)
    consultas: List[str] = Field(default_factory=list, description="Qué consultó el agente para responder")
    fuente: str
    modelo: str


def conversar(mensajes: List[MensajeChat], ctx: Contexto, proveedor: Proveedor) -> AsistenteResponse:
    """Loop del agente: el modelo pide herramientas, se ejecutan, y se repite
    hasta que responde en texto (o se acaban los pasos)."""
    historial: Historial = [{"rol": m.rol, "texto": m.texto} for m in mensajes[-MAX_MENSAJES_HISTORIAL:]]
    while historial and historial[0]["rol"] != "usuario":
        historial.pop(0)
    if not historial:
        raise HTTPException(status_code=422, detail="La conversación debe incluir un mensaje del usuario.")

    sistema = sistema_chat(ctx.ahora)
    respuesta = ""
    for _ in range(MAX_PASOS_AGENTE):
        turno = proveedor.paso(sistema, historial, HERRAMIENTAS)
        historial.append({"rol": "asistente", "texto": turno.texto, "llamadas": turno.llamadas, "crudo": turno.crudo})
        if not turno.llamadas:
            respuesta = turno.texto
            break
        resultados = [ejecutar_herramienta(ctx, ll) for ll in turno.llamadas]
        logger.info("[asistente] %s", ", ".join(f"{r.nombre}{' (error)' if r.es_error else ''}" for r in resultados))
        historial.append({"rol": "herramientas", "resultados": resultados})
    else:
        respuesta = "Me enredé consultando tus datos. ¿Me lo preguntas de otra forma?"

    if not respuesta:
        respuesta = ("Te dejé la propuesta abajo para que la revises." if ctx.propuestas
                     else "No tengo una respuesta para eso. ¿Me lo preguntas de otra forma?")
    return AsistenteResponse(respuesta=respuesta, propuestas=ctx.propuestas, consultas=ctx.consultas,
                             fuente=proveedor.fuente, modelo=proveedor.modelo("chat"))


# =====================================================================
# Endpoints
# =====================================================================

router = APIRouter(tags=["asistente"])


def dependencias_backend():
    """Las mismas funciones que usa el loop del agente en main.py (paso 3 y 4):
    así el chat ve exactamente la agenda que ve el resto de la app.
    Import perezoso para no crear un ciclo main -> asistente -> main."""
    import main

    return main._peticion_plan, main.planificar


class EstadoAsistente(BaseModel):
    disponible: bool
    fuente: str
    modelo_interpretar: Optional[str]
    modelo_chat: Optional[str]
    mensaje: str


@router.get("/asistente/estado", response_model=EstadoAsistente)
def estado_asistente(proveedor: Optional[Proveedor] = Depends(proveedor_actual)):
    if proveedor is None:
        return EstadoAsistente(
            disponible=False, fuente="reglas", modelo_interpretar=None, modelo_chat=None,
            mensaje="Sin modelo de lenguaje: las tareas se interpretan con reglas y el chat no está disponible.")
    return EstadoAsistente(
        disponible=True, fuente=proveedor.fuente,
        modelo_interpretar=proveedor.modelo("interpretar"), modelo_chat=proveedor.modelo("chat"),
        mensaje=f"Usando {proveedor.fuente}.")


@router.post("/interpretar", response_model=InterpretarResponse)
def endpoint_interpretar(payload: InterpretarRequest,
                         proveedor: Optional[Proveedor] = Depends(proveedor_actual)):
    return interpretar(payload.texto, datetime.now().replace(second=0, microsecond=0), proveedor)


@router.post("/asistente", response_model=AsistenteResponse)
def endpoint_asistente(payload: AsistenteRequest,
                       db: Session = Depends(get_db),
                       proveedor: Optional[Proveedor] = Depends(proveedor_actual),
                       backend=Depends(dependencias_backend)):
    if proveedor is None:
        raise HTTPException(status_code=503, detail=(
            "El asistente necesita un modelo de lenguaje. Configura GEMINI_API_KEY o ANTHROPIC_API_KEY, o inicia Ollama "
            f"con el modelo {MODELO_OLLAMA} (ver docs/asistente.md)."))
    peticion_plan, planificar = backend
    ctx = Contexto(db=db, ahora=datetime.now().replace(second=0, microsecond=0),
                   peticion_plan=peticion_plan, planificar=planificar)
    try:
        return conversar(payload.mensajes, ctx, proveedor)
    except ErrorLLM as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
