import { Confianza, Prioridad, Riesgo, Tipo } from './componentes.jsx'
import { ESTADOS, fmtDuracion, fmtFechaHora, fmtHora, minutosEntre } from './formato.js'

/**
 * Una tarea tal como aparece en la agenda: hora, nombre, duración prevista,
 * confianza de la estimación y, si aplica, el riesgo con sus minutos.
 */
export default function TarjetaTarea({
  tarea,
  item,
  estimacion,
  holgura,
  sinEspacio = false,
  mostrarDia = false,
  children,
}) {
  const enRiesgo = sinEspacio || (holgura !== undefined && holgura !== null)
  const duracion = item ? minutosEntre(item.inicioFecha, item.finFecha) : estimacion?.duracion_esperada_min

  return (
    <article className={`tarjeta ${enRiesgo ? 'en-riesgo' : ''} estado-${tarea.estado}`}>
      <div className="tarjeta-hora">
        {item ? (
          <>
            <span className="hora-inicio">{fmtHora(item.inicioFecha)}</span>
            <span className="hora-fin">{fmtHora(item.finFecha)}</span>
          </>
        ) : (
          <span className="hora-inicio">—</span>
        )}
      </div>

      <div className="tarjeta-cuerpo">
        <h3 className="tarjeta-titulo">{tarea.nombre}</h3>
        <p className="tarjeta-meta">
          {mostrarDia && item && <span>{fmtFechaHora(item.inicioFecha)} · </span>}
          {duracion ? <span>{fmtDuracion(duracion)} previstos</span> : null}
          {estimacion && estimacion.duracion_esperada_min !== tarea.estimacion_usuario_min && (
            <span className="tachado-suave" title="Tu estimación original">
              {' '}
              (tú dijiste {fmtDuracion(tarea.estimacion_usuario_min)})
            </span>
          )}
          <span> · vence {fmtFechaHora(tarea.limite)}</span>
        </p>
        <div className="insignias">
          <Tipo tipo={tarea.tipo} />
          <Prioridad prioridad={tarea.prioridad} />
          <Confianza estimacion={estimacion} />
          {tarea.estado !== 'pendiente' && (
            <span className={`insignia estado-insignia-${tarea.estado}`}>{ESTADOS[tarea.estado]}</span>
          )}
          <Riesgo holgura={holgura} sinEspacio={sinEspacio} />
        </div>
        {children}
      </div>
    </article>
  )
}
