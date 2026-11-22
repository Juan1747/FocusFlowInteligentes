import { useCallback, useEffect, useMemo, useState } from 'react'
import { calcularAgenda } from './api.js'
import { desdeIso } from './formato.js'

/**
 * Carga la agenda vigente y la deja lista para pintar.
 *
 * Devuelve, además de los datos crudos, índices por id de tarea:
 *   tareaPorId, itemPorId (su lugar en la agenda), riesgoPorId (holgura),
 *   sinEspacio (pendientes que el planificador no pudo ubicar).
 */
export function useAgenda() {
  const [datos, setDatos] = useState(null)
  const [error, setError] = useState(null)
  const [cargando, setCargando] = useState(true)

  const recargar = useCallback(async () => {
    setCargando(true)
    setError(null)
    try {
      setDatos(await calcularAgenda())
    } catch (e) {
      setError(e)
    } finally {
      setCargando(false)
    }
  }, [])

  useEffect(() => {
    recargar()
  }, [recargar])

  const indices = useMemo(() => {
    if (!datos) return null
    const tareaPorId = new Map(datos.tareas.map((t) => [t.id, t]))
    const itemPorId = new Map(
      datos.plan.agenda.map((a) => [
        a.tarea_id,
        { ...a, inicioFecha: desdeIso(a.inicio), finFecha: desdeIso(a.fin) },
      ]),
    )
    const riesgoPorId = new Map(datos.plan.en_riesgo.map((r) => [r.tarea_id, r.holgura_min]))
    const sinEspacio = datos.pendientes.filter((t) => !itemPorId.has(t.id))
    const agendaOrdenada = [...itemPorId.values()].sort((a, b) => a.inicioFecha - b.inicioFecha)
    return { tareaPorId, itemPorId, riesgoPorId, sinEspacio, agendaOrdenada }
  }, [datos])

  return { datos, indices, error, cargando, recargar }
}
