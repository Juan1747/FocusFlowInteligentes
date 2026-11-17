from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from database import Base


class Tarea(Base):
    __tablename__ = "tareas"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    tipo = Column(String, nullable=False, index=True)
    limite = Column(DateTime, nullable=False)
    prioridad = Column(Integer, nullable=False)
    estimacion_usuario_min = Column(Integer, nullable=False)
    estado = Column(String, nullable=False, default="pendiente")



class BloqueLibre(Base):
    __tablename__ = "bloques_libres"

    id = Column(Integer, primary_key=True, index=True)
    inicio = Column(DateTime, nullable=False, index=True)
    fin = Column(DateTime, nullable=False)


class Registro(Base):
    __tablename__ = "registros"

    id = Column(Integer, primary_key=True, index=True)
    tarea_id = Column(Integer, ForeignKey("tareas.id"), nullable=False, index=True)
    tipo = Column(String, nullable=False, index=True)
    estimacion_min = Column(Integer, nullable=False)
    duracion_real_min = Column(Integer, nullable=False)
    terminada_en = Column(DateTime, nullable=False)