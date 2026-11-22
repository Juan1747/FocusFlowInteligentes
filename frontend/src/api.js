// Cliente de la API de FocusFlow (contrato US-C1).
//
// Todas las rutas van por /api: el servidor de Vite las reenvía al backend
// (ver vite.config.js). Las fechas viajan en ISO 8601, hora local, sin zona.

import { aIsoLocal, desdeIso } from './formato.js'

const BASE = '/api'

export class ErrorApi extends Error {
  constructor(mensaje, estado) {
    super(mensaje)
    this.estado = estado
  }
}

async function llamar(metodo, ruta, cuerpo) {
  let respuesta
  try {
    respuesta = await fetch(BASE + ruta, {
      method: metodo,
      headers: cuerpo ? { 'Content-Type': 'application/json' } : undefined,
      body: cuerpo ? JSON.stringify(cuerpo) : undefined,
    })
  } catch {
    throw new ErrorApi(
      'No hay conexión con el backend. ¿Está corriendo "uvicorn main:app" en el puerto 8000?',
      0,
    )
  }
  if (!respuesta.ok) {
    let detalle = `Error ${respuesta.status}`
    try {
      const datos = await respuesta.json()
      if (typeof datos.detail === 'string') detalle = datos.detail
      else if (Array.isArray(datos.detail)) {
        detalle = datos.detail.map((d) => `${d.loc?.slice(1).join('.')}: ${d.msg}`).join(' · ')
      }
    } catch {
      /* respuesta sin JSON */
    }
    throw new ErrorApi(detalle, respuesta.status)
  }
  return respuesta.status === 204 ? null : respuesta.json()
}

// ---------- Tareas (US-C2) ----------

export const listarTareas = (estado) =>
  llamar('GET', estado ? `/tareas?estado=${encodeURIComponent(estado)}` : '/tareas')

export const crearTarea = (tarea) => llamar('POST', '/tareas', tarea)

export const editarTarea = (id, cambios) => llamar('PATCH', `/tareas/${id}`, cambios)

// US-C4/US-C5: devuelve la agenda nueva con la explicación del agente.
export const terminarTarea = (id, inicio, fin) =>
  llamar('POST', `/tareas/${id}/terminar`, { inicio: aIsoLocal(inicio), fin: aIsoLocal(fin) })

// ---------- Bloques libres (US-C3) ----------

export const listarBloques = (dia) =>
  llamar('GET', dia ? `/bloques-libres?dia=${dia}` : '/bloques-libres')

// Reemplaza los bloques que ese día ya tuviera.
export const guardarBloquesDia = (dia, bloques) =>
  llamar('POST', '/bloques-libres', { dia, bloques })

// ---------- Estimador y planificador ----------

export const estimar = (tipo, estimacionUsuarioMin) =>
  llamar('POST', '/estimar', { tipo, estimacion_usuario_min: estimacionUsuarioMin })

export const planificar = (peticion) => llamar('POST', '/planificar', peticion)

// ---------- Agenda actual ----------

const DESCANSO_MIN = 15 // el mismo que usa el agente

/**
 * Calcula la agenda vigente desde `desde` (por defecto, ahora).
 *
 * Hace lo mismo que los pasos 3 y 4 del loop del agente (main.py): re-estima
 * las tareas no terminadas, toma los bloques libres que quedan desde `desde` y
 * pide la agenda al planificador. Se hace aquí porque el backend todavía no
 * expone un GET /agenda; cuando exista, esta función se reduce a una llamada.
 */
export async function calcularAgenda(desde = new Date()) {
  const [tareas, bloques] = await Promise.all([listarTareas(), listarBloques()])
  const pendientes = tareas.filter((t) => t.estado !== 'terminada')

  const estimacionesLista = await Promise.all(
    pendientes.map((t) => estimar(t.tipo, t.estimacion_usuario_min)),
  )
  const estimaciones = new Map(pendientes.map((t, i) => [t.id, estimacionesLista[i]]))

  // Redondear "desde" al minuto siguiente: nunca agendar en el pasado.
  const corte = new Date(desde)
  if (corte.getSeconds() || corte.getMilliseconds()) {
    corte.setSeconds(0, 0)
    corte.setMinutes(corte.getMinutes() + 1)
  }
  const bloquesPlan = bloques
    .map((b) => ({ inicio: desdeIso(b.inicio), fin: desdeIso(b.fin) }))
    .filter((b) => b.fin > corte)
    .map((b) => ({ inicio: b.inicio > corte ? b.inicio : corte, fin: b.fin }))
    .filter((b) => b.fin > b.inicio)
    .map((b) => ({ inicio: aIsoLocal(b.inicio), fin: aIsoLocal(b.fin) }))

  let plan = { factible: true, agenda: [], en_riesgo: [], explicacion: '' }
  if (pendientes.length) {
    plan = await planificar({
      tareas: pendientes.map((t) => ({
        id: t.id,
        tipo: t.tipo,
        duracion_min: estimaciones.get(t.id).duracion_esperada_min,
        limite: t.limite,
        prioridad: t.prioridad,
      })),
      bloques_libres: bloquesPlan,
      descanso_min: DESCANSO_MIN,
    })
  }

  return { tareas, pendientes, estimaciones, bloques, plan, calculadaEn: new Date(desde) }
}
