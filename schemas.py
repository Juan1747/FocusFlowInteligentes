from datetime import date, datetime, time
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

EstadoTarea = Literal["pendiente", "en_progreso", "terminada"]
Confianza = Literal["alta", "media", "baja"]


# ---------- Tareas (US-C2) ----------

class TareaBase(BaseModel):
    nombre: str = Field(..., min_length=1, examples=["Lectura de Estocásticos"])
    tipo: str = Field(..., min_length=1, examples=["programacion"])
    limite: datetime = Field(..., examples=["2026-11-20T23:59"])
    prioridad: int = Field(..., ge=1, examples=[3])
    estimacion_usuario_min: int = Field(..., gt=0, examples=[90])


class TareaCreate(TareaBase):
    pass


class TareaUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=1)
    tipo: Optional[str] = Field(None, min_length=1)
    limite: Optional[datetime] = None
    prioridad: Optional[int] = Field(None, ge=1)
    estimacion_usuario_min: Optional[int] = Field(None, gt=0)
    estado: Optional[EstadoTarea] = None


class TareaOut(TareaBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    estado: EstadoTarea


# ---------- Bloques libres (US-C3) ----------

class BloqueLibreBase(BaseModel):
    inicio: datetime = Field(..., examples=["2026-11-17T18:00"])
    fin: datetime = Field(..., examples=["2026-11-17T21:00"])

    @model_validator(mode="after")
    def validar_rango(self):
        if self.fin <= self.inicio:
            raise ValueError("fin debe ser posterior a inicio")
        return self


class BloqueLibreCreate(BloqueLibreBase):
    pass


class BloqueLibreOut(BloqueLibreBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class RangoHorario(BaseModel):
    inicio: time = Field(..., examples=["18:00"])
    fin: time = Field(..., examples=["21:00"])

    @model_validator(mode="after")
    def validar_rango(self):
        if self.fin <= self.inicio:
            raise ValueError("fin debe ser posterior a inicio")
        return self


class BloquesDiaCreate(BaseModel):
    dia: date = Field(..., examples=["2026-11-17"])
    bloques: List[RangoHorario]

    @model_validator(mode="after")
    def validar_solapes(self):
        ordenados = sorted(self.bloques, key=lambda b: b.inicio)
        for anterior, siguiente in zip(ordenados, ordenados[1:]):
            if siguiente.inicio < anterior.fin:
                raise ValueError("los bloques de un mismo día no pueden solaparse")
        return self


class BloquesDiaOut(BaseModel):
    dia: date
    bloques: List[BloqueLibreOut]


# ---------- Contrato de API (US-C1) ----------

# POST /estimar
class EstimarRequest(BaseModel):
    tipo: str = Field(..., examples=["programacion"])
    estimacion_usuario_min: int = Field(..., gt=0, examples=[90])


class EstimarResponse(BaseModel):
    duracion_esperada_min: int
    incertidumbre_min: int
    confianza: Confianza
    n_observaciones: int


# POST /estimador/actualizar
class ActualizarEstimadorRequest(BaseModel):
    tipo: str = Field(..., examples=["programacion"])
    duracion_real_min: int = Field(..., gt=0, examples=[180])


class ActualizarEstimadorResponse(BaseModel):
    media_min: int
    incertidumbre_min: int
    n_observaciones: int


# POST /planificar
class TareaPlanificar(BaseModel):
    id: int
    tipo: str
    duracion_min: int = Field(..., gt=0)
    limite: datetime
    prioridad: int = Field(..., ge=1)


class BloquePlanificar(BloqueLibreBase):
    pass


class PlanificarRequest(BaseModel):
    tareas: List[TareaPlanificar]
    bloques_libres: List[BloquePlanificar]
    descanso_min: int = Field(15, ge=0)


class AgendaItem(BaseModel):
    tarea_id: int
    inicio: datetime
    fin: datetime


class TareaEnRiesgo(BaseModel):
    tarea_id: int
    holgura_min: int


class PlanificarResponse(BaseModel):
    factible: bool
    agenda: List[AgendaItem]
    en_riesgo: List[TareaEnRiesgo]
    explicacion: str = ""



# ---------- Terminar tarea (US-C4) ----------

class TerminarTareaRequest(BaseModel):
    inicio: datetime = Field(..., examples=["2026-11-17T18:00"])
    fin: datetime = Field(..., examples=["2026-11-17T21:00"])

    @model_validator(mode="after")
    def validar_rango(self):
        if self.fin <= self.inicio:
            raise ValueError("fin debe ser posterior a inicio")
        return self