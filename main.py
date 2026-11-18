import logging
from datetime import date, datetime, time, timedelta
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from sqlalchemy.orm import Session

import models
import schemas
from database import Base, engine, get_db
from explicacion import generar_explicacion
from estimador import Estimador, estimar_contrato, actualizar_contrato

Base.metadata.create_all(bind=engine)

app = FastAPI(title="FocusFlow API", version="0.1.0")


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("focusflow.agente")


def _rango_dia(dia: date):
    inicio = datetime.combine(dia, time.min)
    return inicio, inicio + timedelta(days=1)


# ---------- Tareas (US-C2) ----------

@app.post("/tareas", response_model=schemas.TareaOut, status_code=status.HTTP_201_CREATED)
def crear_tarea(tarea: schemas.TareaCreate, db: Session = Depends(get_db)):
    db_tarea = models.Tarea(**tarea.model_dump(), estado="pendiente")
    db.add(db_tarea)
    db.commit()
    db.refresh(db_tarea)
    return db_tarea


@app.get("/tareas", response_model=List[schemas.TareaOut])
def listar_tareas(
    estado: Optional[schemas.EstadoTarea] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(models.Tarea)
    if estado is not None:
        query = query.filter(models.Tarea.estado == estado)
    return query.order_by(models.Tarea.limite).all()


@app.get("/tareas/{tarea_id}", response_model=schemas.TareaOut)
def obtener_tarea(tarea_id: int, db: Session = Depends(get_db)):
    db_tarea = db.get(models.Tarea, tarea_id)
    if db_tarea is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return db_tarea


@app.patch("/tareas/{tarea_id}", response_model=schemas.TareaOut)
def editar_tarea(tarea_id: int, cambios: schemas.TareaUpdate, db: Session = Depends(get_db)):
    db_tarea = db.get(models.Tarea, tarea_id)
    if db_tarea is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    for campo, valor in cambios.model_dump(exclude_unset=True).items():
        setattr(db_tarea, campo, valor)
    db.commit()
    db.refresh(db_tarea)
    return db_tarea


# ---------- Bloques libres (US-C3) ----------

@app.post("/bloques-libres", response_model=schemas.BloquesDiaOut, status_code=status.HTTP_201_CREATED)
def registrar_bloques_dia(payload: schemas.BloquesDiaCreate, db: Session = Depends(get_db)):
    """Registra la disponibilidad de un día. Reemplaza los bloques que ese día ya tuviera."""
    inicio_dia, fin_dia = _rango_dia(payload.dia)
    db.query(models.BloqueLibre).filter(
        models.BloqueLibre.inicio >= inicio_dia,
        models.BloqueLibre.inicio < fin_dia,
    ).delete(synchronize_session=False)

    nuevos = [
        models.BloqueLibre(
            inicio=datetime.combine(payload.dia, b.inicio),
            fin=datetime.combine(payload.dia, b.fin),
        )
        for b in sorted(payload.bloques, key=lambda b: b.inicio)
    ]
    db.add_all(nuevos)
    db.commit()
    for bloque in nuevos:
        db.refresh(bloque)
    return schemas.BloquesDiaOut(dia=payload.dia, bloques=nuevos)


@app.get("/bloques-libres", response_model=List[schemas.BloqueLibreOut])
def listar_bloques(dia: Optional[date] = Query(None), db: Session = Depends(get_db)):
    query = db.query(models.BloqueLibre)
    if dia is not None:
        inicio_dia, fin_dia = _rango_dia(dia)
        query = query.filter(
            models.BloqueLibre.inicio >= inicio_dia,
            models.BloqueLibre.inicio < fin_dia,
        )
    return query.order_by(models.BloqueLibre.inicio).all()


# ---------- Estimador real (US-A1 a US-A3, US-A7) + mock del planificador ----------

# Estado del estimador en memoria. Aprende de cada tarea terminada vía el loop
# del agente (US-C5). La persistencia a SQLite queda pendiente.
estimador = Estimador()


@app.post("/estimar", response_model=schemas.EstimarResponse)
def estimar(payload: schemas.EstimarRequest):
    return estimar_contrato(estimador, payload)


@app.post("/estimador/actualizar", response_model=schemas.ActualizarEstimadorResponse)
def actualizar_estimador(payload: schemas.ActualizarEstimadorRequest):
    return actualizar_contrato(estimador, payload)


@app.post("/planificar", response_model=schemas.PlanificarResponse)
def planificar(payload: schemas.PlanificarRequest):
    return schemas.PlanificarResponse(
        factible=True,
        agenda=[
            schemas.AgendaItem(
                tarea_id=1,
                inicio=datetime(2026, 11, 17, 18, 0),
                fin=datetime(2026, 11, 17, 20, 22),
            )
        ],
        en_riesgo=[schemas.TareaEnRiesgo(tarea_id=3, holgura_min=-25)],
    )



# ---------- Terminar tarea + loop del agente (US-C4, US-C5 y US-C6) ----------

def _peticion_plan(db: Session, desde: datetime) -> schemas.PlanificarRequest:
    """Re-estima las tareas pendientes y arma la petición al planificador
    con los bloques libres que quedan a partir de `desde`."""
    pendientes = (
        db.query(models.Tarea)
        .filter(models.Tarea.estado != "terminada")
        .order_by(models.Tarea.limite)
        .all()
    )
    tareas_plan = []
    for t in pendientes:
        estimacion = estimar(
            schemas.EstimarRequest(tipo=t.tipo, estimacion_usuario_min=t.estimacion_usuario_min)
        )
        tareas_plan.append(
            schemas.TareaPlanificar(
                id=t.id,
                tipo=t.tipo,
                duracion_min=estimacion.duracion_esperada_min,
                limite=t.limite,
                prioridad=t.prioridad,
            )
        )

    bloques = (
        db.query(models.BloqueLibre)
        .filter(models.BloqueLibre.fin > desde)
        .order_by(models.BloqueLibre.inicio)
        .all()
    )
    bloques_plan = [
        schemas.BloquePlanificar(inicio=max(b.inicio, desde), fin=b.fin) for b in bloques
    ]
    return schemas.PlanificarRequest(
        tareas=tareas_plan, bloques_libres=bloques_plan, descanso_min=15
    )


@app.post("/tareas/{tarea_id}/terminar", response_model=schemas.PlanificarResponse)
def terminar_tarea(tarea_id: int, payload: schemas.TerminarTareaRequest, db: Session = Depends(get_db)):
    # --- US-C4: marcar terminada y guardar el registro ---
    db_tarea = db.get(models.Tarea, tarea_id)
    if db_tarea is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    if db_tarea.estado == "terminada":
        raise HTTPException(status_code=409, detail="La tarea ya estaba terminada")

    duracion_estimada_min = db_tarea.estimacion_usuario_min
    duracion_real_min = max(1, round((payload.fin - payload.inicio).total_seconds() / 60))

    db_tarea.estado = "terminada"
    registro = models.Registro(
        tarea_id=db_tarea.id,
        tipo=db_tarea.tipo,
        estimacion_min=duracion_estimada_min,
        duracion_real_min=duracion_real_min,
        terminada_en=payload.fin,
    )
    db.add(registro)
    db.commit()
    db.refresh(registro)

    # --- US-C5: loop del agente ---
    # 1. Percibir
    logger.info(
        "[1 percibir] tarea %s (%s) terminada: real=%s min, estimado=%s min",
        db_tarea.id, db_tarea.tipo, duracion_real_min, duracion_estimada_min,
    )

    # Agenda vieja (US-C6): el plan que había si la tarea hubiera durado lo estimado,
    # calculado ANTES de actualizar el estimador.
    fin_previsto = payload.inicio + timedelta(minutes=duracion_estimada_min)
    agenda_vieja = planificar(_peticion_plan(db, desde=fin_previsto))
    logger.info(
        "[1 percibir] agenda vieja: %s tareas agendadas, %s en riesgo",
        len(agenda_vieja.agenda), len(agenda_vieja.en_riesgo),
    )

    # 2. Actualizar el modelo del mundo
    estado_estimador = actualizar_estimador(
        schemas.ActualizarEstimadorRequest(tipo=db_tarea.tipo, duracion_real_min=duracion_real_min)
    )
    logger.info(
        "[2 actualizar] %s: media=%s min, incertidumbre=%s min, n=%s",
        db_tarea.tipo, estado_estimador.media_min,
        estado_estimador.incertidumbre_min, estado_estimador.n_observaciones,
    )

    # 3. Recalcular estimaciones de las tareas pendientes
    peticion = _peticion_plan(db, desde=payload.fin)
    logger.info("[3 recalcular] %s tareas pendientes re-estimadas", len(peticion.tareas))

    # 4. Decidir: nueva agenda con los bloques libres que quedan desde el fin real
    agenda_nueva = planificar(peticion)
    logger.info(
        "[4 decidir] %s bloques libres -> factible=%s, %s tareas agendadas, %s en riesgo",
        len(peticion.bloques_libres), agenda_nueva.factible,
        len(agenda_nueva.agenda), len(agenda_nueva.en_riesgo),
    )

    # 5. Explicar y publicar (US-C6)
    explicacion = generar_explicacion(
        db_tarea,
        duracion_estimada_min,
        duracion_real_min,
        agenda_vieja,
        agenda_nueva,
        nombres={t.id: t.nombre for t in db.query(models.Tarea).all()},
    )
    logger.info("[5 publicar] %s", explicacion)
    return agenda_nueva.model_copy(update={"explicacion": explicacion})