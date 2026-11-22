// Piezas de interfaz compartidas por las cinco pantallas.

import { UMBRAL_RIESGO_MIN } from './constantes.js'
import { etiquetaPrioridad, etiquetaTipo, textoHolgura } from './formato.js'

export function Encabezado({ titulo, children, acciones }) {
  return (
    <div className="encabezado">
      <div>
        <h1>{titulo}</h1>
        {children && <p className="subtitulo">{children}</p>}
      </div>
      {acciones && <div className="encabezado-acciones">{acciones}</div>}
    </div>
  )
}

export function Cargando({ texto = 'Cargando…' }) {
  return (
    <div className="cargando" role="status">
      <span className="spinner" aria-hidden="true" />
      {texto}
    </div>
  )
}

export function CajaError({ error, alReintentar }) {
  if (!error) return null
  return (
    <div className="caja caja-error" role="alert">
      <strong>No se pudo completar la acción.</strong>
      <p>{error.message ?? String(error)}</p>
      {alReintentar && (
        <button type="button" className="boton secundario" onClick={alReintentar}>
          Reintentar
        </button>
      )}
    </div>
  )
}

export function Vacio({ titulo, children, accion }) {
  return (
    <div className="vacio">
      <p className="vacio-titulo">{titulo}</p>
      {children && <p>{children}</p>}
      {accion}
    </div>
  )
}

/** Nivel de confianza del estimador (US-A7), visible por tarea (US-B5). */
export function Confianza({ estimacion }) {
  if (!estimacion) return null
  const { confianza, n_observaciones: n } = estimacion
  const titulo =
    n === 0
      ? 'Sin historial de este tipo: se usa tu estimación'
      : `Basado en ${n} tarea${n === 1 ? '' : 's'} de este tipo`
  return (
    <span className={`insignia confianza-${confianza}`} title={titulo}>
      Confianza {confianza}
    </span>
  )
}

/** Marca de tarea en riesgo (US-B4): siempre con los minutos, no solo color. */
export function Riesgo({ holgura, sinEspacio = false }) {
  if (sinEspacio) {
    return <span className="insignia riesgo-alto">Sin espacio en la agenda</span>
  }
  if (holgura === undefined || holgura === null) return null
  const clase = holgura < 0 ? 'riesgo-alto' : 'riesgo-medio'
  return (
    <span
      className={`insignia ${clase}`}
      title={`En riesgo: menos de ${UMBRAL_RIESGO_MIN} min de margen sobre el plazo`}
    >
      {holgura < 0 ? 'Atrasada · ' : 'Justa · '}
      {textoHolgura(holgura)}
    </span>
  )
}

export function Tipo({ tipo }) {
  return <span className="insignia neutra">{etiquetaTipo(tipo)}</span>
}

export function Prioridad({ prioridad }) {
  return (
    <span className={`insignia prioridad-${Math.min(prioridad, 3)}`}>
      Prioridad {etiquetaPrioridad(prioridad).toLowerCase()}
    </span>
  )
}
