"""
FocusFlow — Pruebas del asistente (interpretar + chat con herramientas).

No llaman a ningún modelo real: usan un proveedor simulado con un guion fijo y
una base SQLite en memoria, así que corren gratis, sin internet y sin tocar
focusflow.db.

Ejecutar:
    python prueba_asistente.py      (o: pytest prueba_asistente.py)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import asistente as a
import models
import schemas
from database import Base, get_db
from planificador import planificar_contrato

AHORA = datetime(2026, 11, 23, 10, 0)  # lunes


# ---------- Utilidades ----------

def base_en_memoria():
    motor = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=motor)
    return sessionmaker(bind=motor, autoflush=False)


def sembrar(db):
    db.add_all([
        models.Tarea(id=1, nombre="Taller de Cálculo", tipo="taller", limite=AHORA.replace(hour=23, minute=59),
                     prioridad=3, estimacion_usuario_min=90, estado="pendiente"),
        models.Tarea(id=2, nombre="Lectura de Estocásticos", tipo="lectura", limite=AHORA + timedelta(days=1),
                     prioridad=2, estimacion_usuario_min=60, estado="pendiente"),
        models.BloqueLibre(inicio=AHORA.replace(hour=14), fin=AHORA.replace(hour=18)),
        models.BloqueLibre(inicio=AHORA.replace(hour=19), fin=AHORA.replace(hour=21)),
        models.Registro(tarea_id=1, tipo="taller", estimacion_min=60, duracion_real_min=120,
                        terminada_en=AHORA - timedelta(days=1)),
    ])
    db.commit()


def peticion_plan_prueba(db, desde):
    """Igual que main._peticion_plan, con el estimador simulado: real = 1,5 x lo que dice el usuario."""
    tareas = db.query(models.Tarea).filter(models.Tarea.estado != "terminada").all()
    bloques = db.query(models.BloqueLibre).filter(models.BloqueLibre.fin > desde).all()
    return schemas.PlanificarRequest(
        tareas=[schemas.TareaPlanificar(id=t.id, tipo=t.tipo, duracion_min=round(t.estimacion_usuario_min * 1.5),
                                        limite=t.limite, prioridad=t.prioridad) for t in tareas],
        bloques_libres=[schemas.BloquePlanificar(inicio=max(b.inicio, desde), fin=b.fin) for b in bloques],
        descanso_min=15,
    )


def contexto(db):
    return a.Contexto(db=db, ahora=AHORA, peticion_plan=peticion_plan_prueba, planificar=planificar_contrato)


class Simulado(a.Proveedor):
    """Proveedor con guion: devuelve los turnos en orden y guarda lo que recibió."""

    fuente = "simulado"

    def __init__(self, turnos=(), json_respuesta=None, falla=False):
        self.turnos = list(turnos)
        self.json_respuesta = json_respuesta
        self.falla = falla
        self.historiales = []

    def modelo(self, uso):
        return f"simulado-{uso}"

    def json(self, sistema, texto, esquema):
        if self.falla:
            raise a.ErrorLLM("Sin conexión simulada.")
        return self.json_respuesta

    def paso(self, sistema, historial, herramientas):
        self.historiales.append([dict(h) for h in historial])
        return self.turnos.pop(0)


def llamada(herramienta, /, **argumentos):
    return a.LlamadaHerramienta(id=f"id_{herramienta}", nombre=herramienta, argumentos=argumentos)


# ---------- Interpretar ----------

def prueba_reglas_frase_completa():
    r = a.interpretar("Tengo que entregar el taller de cálculo mañana a las 7 pm, me toma 2 horas, es urgente",
                      AHORA, None)
    t = r.tarea
    assert r.fuente == "reglas" and r.faltantes == []
    assert t.tipo == "taller" and t.prioridad == 3 and t.estimacion_usuario_min == 120
    assert t.limite == datetime(2026, 11, 24, 19, 0)
    assert "cálculo" in t.nombre.lower()


def prueba_reglas_marca_lo_que_falta():
    r = a.interpretar("leer algo de historia", AHORA, None)
    assert set(r.faltantes) == {"limite", "estimacion_usuario_min"}
    assert r.tarea.limite is None and r.tarea.estimacion_usuario_min is None
    assert r.tarea.prioridad == 2  # sin decir nada: media


def prueba_validacion_de_lo_que_dice_el_llm():
    """Lo que devuelve el modelo nunca se usa sin validar."""
    crudo = {"nombre": "  ", "tipo": "deporte", "fecha_limite": "2026-11-20",  # pasada
             "hora_limite": "25:00", "prioridad": 7, "estimacion_min": 100000}
    tarea, faltantes = a.normalizar_interpretacion(crudo, AHORA)
    assert tarea.tipo in a.TIPOS
    assert tarea.prioridad == 2
    assert set(faltantes) == {"nombre", "limite", "estimacion_usuario_min"}

    bien = {"nombre": "parcial de física", "tipo": "estudio_parcial", "fecha_limite": "2026-11-27",
            "hora_limite": None, "prioridad": 3, "estimacion_min": 180}
    tarea, faltantes = a.normalizar_interpretacion(bien, AHORA)
    assert faltantes == [] and tarea.nombre == "Parcial de física"
    assert tarea.limite == datetime(2026, 11, 27, 23, 59)  # sin hora: fin del día


def prueba_interpretar_con_llm_y_respaldo():
    llm = Simulado(json_respuesta={"nombre": "Proyecto de IA", "tipo": "programacion", "fecha_limite": "2026-11-26",
                                   "hora_limite": "18:00", "prioridad": 3, "estimacion_min": 240})
    r = a.interpretar("el proyecto de IA para el jueves a las 6", AHORA, llm)
    assert r.fuente == "simulado" and r.modelo == "simulado-interpretar" and r.aviso is None
    assert r.tarea.limite == datetime(2026, 11, 26, 18, 0)

    caido = Simulado(falla=True)
    r = a.interpretar("leer el capítulo 3 para mañana, 1 hora", AHORA, caido)
    assert r.fuente == "reglas" and "intérprete básico" in r.aviso
    assert r.tarea.tipo == "lectura" and r.tarea.estimacion_usuario_min == 60


# ---------- Herramientas ----------

def prueba_ver_agenda_usa_el_planificador():
    Sesion = base_en_memoria()
    with Sesion() as db:
        sembrar(db)
        salida = a.h_ver_agenda(contexto(db), {})
    nombres = [x["nombre"] for x in salida["agenda"]]
    assert "Taller de Cálculo" in nombres
    taller = next(x for x in salida["agenda"] if x["nombre"] == "Taller de Cálculo")
    assert taller["duracion_prevista_min"] == 135 and taller["usuario_estimo_min"] == 90
    assert taller["inicio"].startswith("2026-11-23T14:00")  # lo decidió el CSP, no el LLM
    assert salida["todas_caben"] is True


def prueba_historial_por_tipo():
    Sesion = base_en_memoria()
    with Sesion() as db:
        sembrar(db)
        salida = a.h_ver_historial(contexto(db), {})
    assert salida["por_tipo"][0] == {"tipo": "taller", "tareas_terminadas": 1, "estimado_medio_min": 60,
                                     "real_medio_min": 120, "factor_medio": 2.0}


def prueba_propuestas_se_validan_y_no_cambian_nada():
    Sesion = base_en_memoria()
    with Sesion() as db:
        sembrar(db)
        ctx = contexto(db)

        salida = a.h_proponer_crear(ctx, {"nombre": "Quiz de inglés", "tipo": "estudio_parcial",
                                          "fecha_limite": "2026-11-25", "hora_limite": None,
                                          "prioridad": 2, "estimacion_min": 30})
        p = ctx.propuestas[-1]
        assert salida["propuesta_id"] == p.id and p.accion == "crear_tarea"
        schemas.TareaCreate(**p.datos)  # la interfaz podrá enviarla tal cual a POST /tareas
        assert p.datos["limite"] == "2026-11-25T23:59:00"

        a.h_proponer_editar(ctx, {"tarea_id": 2, "nombre": None, "fecha_limite": "2026-11-26",
                                  "hora_limite": None, "prioridad": 3, "estimacion_min": None})
        p = ctx.propuestas[-1]
        assert p.tarea_id == 2 and p.datos == {"limite": "2026-11-26T10:00:00", "prioridad": 3}  # conserva la hora
        assert "media → alta" in p.resumen

        a.h_proponer_bloque(ctx, {"dia": "2026-11-23", "inicio": "21:30", "fin": "23:00"})
        p = ctx.propuestas[-1]
        assert [b["inicio"] for b in p.datos["bloques"]] == ["14:00:00", "19:00:00", "21:30:00"]  # conserva los que había

        assert db.query(models.Tarea).count() == 2 and db.query(models.BloqueLibre).count() == 2  # nada aplicado


def prueba_errores_vuelven_al_modelo():
    Sesion = base_en_memoria()
    with Sesion() as db:
        sembrar(db)
        ctx = contexto(db)
        casos = [
            llamada("proponer_agregar_bloque_libre", dia="2026-11-23", inicio="15:00", fin="16:00"),  # se solapa
            llamada("proponer_crear_tarea", nombre="X", tipo="taller", fecha_limite="2026-11-01",       # ya pasó
                    hora_limite=None, prioridad=2, estimacion_min=30),
            llamada("proponer_editar_tarea", tarea_id=99, nombre=None, fecha_limite=None, hora_limite=None,
                    prioridad=1, estimacion_min=None),
            llamada("borrar_todo"),
        ]
        resultados = [a.ejecutar_herramienta(ctx, c) for c in casos]
    assert all(r.es_error for r in resultados)
    assert "solaparse" in resultados[0].contenido
    assert "ya pasó" in resultados[1].contenido
    assert "No existe la tarea 99" in resultados[2].contenido
    assert ctx.propuestas == []


def prueba_esquemas_compatibles_con_strict():
    """Claude exige additionalProperties: false y todo en required."""
    for h in a.HERRAMIENTAS:
        p = h.parametros
        assert p["additionalProperties"] is False and set(p["required"]) == set(p["properties"]), h.nombre
    e = a.ESQUEMA_INTERPRETACION
    assert e["additionalProperties"] is False and set(e["required"]) == set(e["properties"])


# ---------- Loop del agente ----------

def prueba_loop_del_agente():
    Sesion = base_en_memoria()
    llm = Simulado([
        a.Turno("", [llamada("ver_agenda")]),
        a.Turno("", [llamada("proponer_agregar_bloque_libre", dia="2026-11-24", inicio="18:00", fin="20:00")]),
        a.Turno("Hoy te alcanza para el taller. Te propongo 2 h libres mañana; confírmalo abajo."),
    ])
    with Sesion() as db:
        sembrar(db)
        respuesta = a.conversar([a.MensajeChat(rol="usuario", texto="¿Me alcanza el tiempo?")], contexto(db), llm)
    assert respuesta.respuesta.startswith("Hoy te alcanza")
    assert respuesta.consultas == ["tu agenda", "propuso agregar tiempo libre"]
    assert [p.accion for p in respuesta.propuestas] == ["bloques_dia"]
    # En la segunda vuelta el modelo recibió el resultado real de ver_agenda.
    resultado = llm.historiales[1][-1]["resultados"][0]
    assert resultado.nombre == "ver_agenda" and "Taller de Cálculo" in json.loads(resultado.contenido)["agenda"][0]["nombre"]


def prueba_loop_se_detiene():
    Sesion = base_en_memoria()
    llm = Simulado([a.Turno("", [llamada("ver_tareas", estado="pendientes")])] * (a.MAX_PASOS_AGENTE + 1))
    with Sesion() as db:
        sembrar(db)
        respuesta = a.conversar([a.MensajeChat(rol="usuario", texto="hola")], contexto(db), llm)
    assert "otra forma" in respuesta.respuesta
    assert len(llm.historiales) == a.MAX_PASOS_AGENTE


# ---------- Proveedores (sin red: se intercepta la llamada) ----------

class _Bloque:
    def __init__(self, **campos):
        self.__dict__.update(campos)


def prueba_proveedor_claude_arma_la_peticion():
    p = a.ProveedorClaude.__new__(a.ProveedorClaude)
    import anthropic
    p._anthropic = anthropic
    enviados = []

    class Mensajes:
        def create(self, **kw):
            enviados.append(kw)
            if "tools" in kw:
                return _Bloque(stop_reason="tool_use", content=[
                    _Bloque(type="thinking", thinking=""),
                    _Bloque(type="tool_use", id="toolu_1", name="ver_agenda", input={}),
                ])
            return _Bloque(stop_reason="end_turn", content=[_Bloque(type="text", text='{"nombre": "X"}')])

    p.cliente = _Bloque(messages=Mensajes(), beta=_Bloque(messages=Mensajes()))

    assert p.json("sistema", "frase", a.ESQUEMA_INTERPRETACION) == {"nombre": "X"}
    assert enviados[0]["model"] == "claude-haiku-5-5"
    assert enviados[0]["output_config"]["format"]["type"] == "json_schema"

    historial = [{"rol": "usuario", "texto": "hola"}]
    turno = p.paso("sistema", historial, a.HERRAMIENTAS)
    kw = enviados[1]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert all(t["strict"] for t in kw["tools"])
    assert [ll.nombre for ll in turno.llamadas] == ["ver_agenda"]

    # La siguiente vuelta reenvía el contenido original (con thinking) y el tool_result.
    historial += [{"rol": "asistente", "texto": "", "llamadas": turno.llamadas, "crudo": turno.crudo},
                  {"rol": "herramientas", "resultados": [a.ResultadoHerramienta("toolu_1", "ver_agenda", "{}")]}]
    mensajes = p._mensajes(historial)
    assert mensajes[1]["content"] is turno.crudo
    assert mensajes[2]["content"][0] == {"type": "tool_result", "tool_use_id": "toolu_1", "content": "{}",
                                         "is_error": False}


def prueba_proveedor_ollama_arma_la_peticion():
    import requests
    enviados = []

    class Respuesta:
        def __init__(self, mensaje):
            self.mensaje = mensaje

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": self.mensaje}

    def post_falso(url, json, timeout):
        enviados.append(json)
        if "tools" in json:
            return Respuesta({"content": "", "tool_calls": [
                {"function": {"name": "ver_tareas", "arguments": {"estado": "pendientes"}}}]})
        return Respuesta({"content": '{"nombre": "Y"}'})

    original = requests.post
    requests.post = post_falso
    try:
        p = a.ProveedorOllama(modelo="qwen2.5:3b", url="http://local")
        assert p.json("s", "t", a.ESQUEMA_INTERPRETACION) == {"nombre": "Y"}
        assert enviados[0]["format"] == a.ESQUEMA_INTERPRETACION
        turno = p.paso("s", [{"rol": "usuario", "texto": "hola"}], a.HERRAMIENTAS)
        assert turno.llamadas[0].nombre == "ver_tareas" and turno.llamadas[0].argumentos == {"estado": "pendientes"}
        assert enviados[1]["messages"][0] == {"role": "system", "content": "s"}
        assert {t["function"]["name"] for t in enviados[1]["tools"]} == {h.nombre for h in a.HERRAMIENTAS}
    finally:
        requests.post = original


def _recorrer(esquema):
    yield esquema
    for hijo in list(esquema.get("properties", {}).values()) + ([esquema["items"]] if "items" in esquema else []):
        yield from _recorrer(hijo)


def prueba_esquemas_para_gemini():
    convertido = a.esquema_gemini(a.ESQUEMA_INTERPRETACION)
    assert convertido["properties"]["fecha_limite"] == {"type": "STRING", "description": "AAAA-MM-DD", "nullable": True}
    assert convertido["properties"]["tipo"]["enum"] == list(a.TIPOS)
    prioridad = convertido["properties"]["prioridad"]
    assert "enum" not in prioridad and "1, 2, 3" in prioridad["description"]  # Gemini no admite enum numérico
    for esquema in [a.ESQUEMA_INTERPRETACION] + [h.parametros for h in a.HERRAMIENTAS]:
        for nodo in _recorrer(a.esquema_gemini(esquema)):
            assert not {"anyOf", "additionalProperties"} & set(nodo), nodo
            assert nodo.get("type", "X").isupper()


def prueba_proveedor_gemini_arma_la_peticion():
    import requests
    enviados, respuestas = [], []

    class Respuesta:
        def __init__(self, codigo, datos):
            self.status_code, self.datos, self.text = codigo, datos, json.dumps(datos)

        def json(self):
            return self.datos

    def post_falso(url, json, headers, timeout):
        enviados.append({"url": url, "cuerpo": json, "headers": headers})
        return respuestas.pop(0)

    def candidato(*partes):
        return Respuesta(200, {"candidates": [{"content": {"role": "model", "parts": list(partes)},
                                               "finishReason": "STOP"}]})

    original_post, original_sleep = requests.post, a.reloj.sleep
    requests.post, a.reloj.sleep = post_falso, lambda s: None
    try:
        p = a.ProveedorGemini(clave="clave-falsa", modelo_interpretar="lite", modelo_chat="flash", url="http://g")

        # Interpretar: salida JSON con esquema convertido; la clave va en el encabezado, no en la URL.
        respuestas.append(candidato({"text": '{"nombre": "Y"}'}))
        assert p.json("s", "t", a.ESQUEMA_INTERPRETACION) == {"nombre": "Y"}
        assert enviados[0]["url"] == "http://g/models/lite:generateContent"
        assert enviados[0]["headers"] == {"x-goog-api-key": "clave-falsa"} and "clave" not in enviados[0]["url"]
        config = enviados[0]["cuerpo"]["generationConfig"]
        assert config["responseMimeType"] == "application/json"
        assert config["responseSchema"] == a.esquema_gemini(a.ESQUEMA_INTERPRETACION)

        # Chat: pide una herramienta; la firma de razonamiento se reenvía en el siguiente paso.
        respuestas.append(candidato({"functionCall": {"name": "ver_tareas", "args": {"estado": "pendientes"}},
                                     "thoughtSignature": "firma"}))
        turno = p.paso("s", [{"rol": "usuario", "texto": "hola"}], a.HERRAMIENTAS)
        assert enviados[1]["url"].endswith("/models/flash:generateContent")
        assert {f["name"] for f in enviados[1]["cuerpo"]["tools"][0]["functionDeclarations"]} == \
            {h.nombre for h in a.HERRAMIENTAS}
        (llamada,) = turno.llamadas
        assert llamada.nombre == "ver_tareas" and llamada.argumentos == {"estado": "pendientes"}

        historial = [{"rol": "usuario", "texto": "hola"},
                     {"rol": "asistente", "texto": turno.texto, "llamadas": turno.llamadas, "crudo": turno.crudo},
                     {"rol": "herramientas", "resultados": [
                         a.ResultadoHerramienta(llamada.id, "ver_tareas", '{"tareas": []}')]}]
        respuestas.append(candidato({"text": "No tienes tareas."}))
        assert p.paso("s", historial, a.HERRAMIENTAS).texto == "No tienes tareas."
        contenidos = enviados[2]["cuerpo"]["contents"]
        assert contenidos[1]["parts"][0]["thoughtSignature"] == "firma"
        assert contenidos[2] == {"role": "user", "parts": [
            {"functionResponse": {"name": "ver_tareas", "response": {"tareas": []}}}]}

        # Cuota: un 429 con retryDelay se reintenta una vez; dos seguidos son un error claro.
        cuota = Respuesta(429, {"error": {"details": [{"retryDelay": "1s"}]}})
        respuestas.extend([cuota, candidato({"text": '{"nombre": "Z"}'})])
        assert p.json("s", "t", a.ESQUEMA_INTERPRETACION) == {"nombre": "Z"}
        respuestas.extend([cuota, cuota])
        try:
            p.json("s", "t", a.ESQUEMA_INTERPRETACION)
            raise AssertionError("debía fallar")
        except a.ErrorLLM as e:
            assert "cuota" in str(e)
        respuestas.append(Respuesta(400, {"error": {"status": "INVALID_ARGUMENT", "message": "API_KEY_INVALID"}}))
        try:
            p.json("s", "t", a.ESQUEMA_INTERPRETACION)
            raise AssertionError("debía fallar")
        except a.ErrorLLM as e:
            assert "clave" in str(e)
    finally:
        requests.post, a.reloj.sleep = original_post, original_sleep


def prueba_eleccion_de_proveedor():
    variables = ("FOCUSFLOW_LLM", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE",
                 "GEMINI_API_KEY", "GOOGLE_API_KEY")
    guardadas = {v: os.environ.pop(v, None) for v in variables}
    original = a.ProveedorOllama.disponible
    a.ProveedorOllama.disponible = staticmethod(lambda *x, **k: False)
    try:
        assert a.elegir_proveedor() is None
        os.environ["GEMINI_API_KEY"] = "clave-falsa"
        assert isinstance(a.elegir_proveedor(), a.ProveedorGemini)
        os.environ["FOCUSFLOW_LLM"] = "reglas"
        assert a.elegir_proveedor() is None
    finally:
        a.ProveedorOllama.disponible = original
        for v, valor in guardadas.items():
            os.environ.pop(v, None)
            if valor is not None:
                os.environ[v] = valor


# ---------- Endpoints ----------

def cliente(proveedor):
    Sesion = base_en_memoria()
    with Sesion() as db:
        sembrar(db)
    app = FastAPI()
    app.include_router(a.router)

    def db_prueba():
        with Sesion() as db:
            yield db

    app.dependency_overrides[get_db] = db_prueba
    app.dependency_overrides[a.proveedor_actual] = lambda: proveedor
    app.dependency_overrides[a.dependencias_backend] = lambda: (peticion_plan_prueba, planificar_contrato)
    return TestClient(app)


def prueba_endpoints():
    sin_llm = cliente(None)
    r = sin_llm.get("/asistente/estado")
    assert r.status_code == 200 and r.json()["disponible"] is False
    r = sin_llm.post("/interpretar", json={"texto": "taller de física para mañana, 1 hora"})
    assert r.status_code == 200 and r.json()["fuente"] == "reglas" and r.json()["tarea"]["tipo"] == "taller"
    r = sin_llm.post("/asistente", json={"mensajes": [{"rol": "usuario", "texto": "hola"}]})
    assert r.status_code == 503

    con_llm = cliente(Simulado([a.Turno("", [llamada("ver_tareas", estado="pendientes")]),
                                a.Turno("Tienes 2 tareas pendientes.")]))
    r = con_llm.post("/asistente", json={"mensajes": [{"rol": "usuario", "texto": "¿qué tengo?"}]})
    assert r.status_code == 200, r.text
    assert r.json()["respuesta"] == "Tienes 2 tareas pendientes." and r.json()["consultas"] == ["tus tareas"]
    r = con_llm.post("/asistente", json={"mensajes": []})
    assert r.status_code == 422


PRUEBAS = [v for k, v in dict(globals()).items() if k.startswith("prueba_") and callable(v)]
globals().update({f"test_{p.__name__[len('prueba_'):]}": p for p in PRUEBAS})  # para pytest


def main():
    for prueba in PRUEBAS:
        prueba()
        print(f"  ok  {prueba.__name__}")
    print(f"\n{len(PRUEBAS)}/{len(PRUEBAS)} pruebas pasan")


if __name__ == "__main__":
    main()
