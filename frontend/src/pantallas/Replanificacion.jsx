import { Encabezado, Riesgo, Vacio } from '../componentes.jsx'
import { desdeIso, etiquetaTipo, fmtDuracion, fmtFechaHora, minutosEntre } from '../formato.js'

/**
 * Compara la agenda que había con la que devolvió el agente y clasifica cada
 * tarea: se movió (y cuánto), ya no cabe, entró, o sigue igual.
 */
function compararAgendas(antes, despues, terminadaId) {
  const viejo = new Map(antes.agenda.map((a) => [a.tarea_id, a]))
  const nuevo = new Map(despues.agenda.map((a) => [a.tarea_id, a]))
  const riesgoViejo = new Map(antes.en_riesgo.map((r) => [r.tarea_id, r.holgura_min]))
  const riesgoNuevo = new Map(despues.en_riesgo.map((r) => [r.tarea_id, r.holgura_min]))
  const ids = new Set([...viejo.keys(), ...nuevo.keys(), ...riesgoNuevo.keys()])
  ids.delete(terminadaId) // la que acaba de terminar ya no se planifica

  const filas = []
  for (const id of ids) {
    const a = viejo.get(id)
    const d = nuevo.get(id)
    let cambio
    let desplazamiento = 0
    if (a && d) {
      desplazamiento = minutosEntre(a.inicio, d.inicio)
      cambio = desplazamiento === 0 ? 'igual' : desplazamiento > 0 ? 'despues' : 'antes'
    } else if (a && !d) {
      cambio = 'fuera'
    } else if (!a && d) {
      cambio = 'entra'
    } else {
      cambio = 'sigue-fuera'
    }
    filas.push({
      id,
      antes: a,
      despues: d,
      cambio,
      desplazamiento,
      riesgoAntes: riesgoViejo.get(id),
      riesgoDespues: riesgoNuevo.get(id),
    })
  }
  const orden = { fuera: 0, despues: 1, antes: 2, entra: 3, 'sigue-fuera': 4, igual: 5 }
  return filas.sort(
    (x, y) =>
      orden[x.cambio] - orden[y.cambio] ||
      (x.despues?.inicio ?? x.antes?.inicio ?? '').localeCompare(y.despues?.inicio ?? y.antes?.inicio ?? ''),
  )
}

function textoCambio(fila) {
  switch (fila.cambio) {
    case 'despues':
      return `${fmtDuracion(fila.desplazamiento)} más tarde`
    case 'antes':
      return `${fmtDuracion(fila.desplazamiento)} más temprano`
    case 'fuera':
      return 'Ya no cabe'
    case 'entra':
      return 'Ahora sí cabe'
    case 'sigue-fuera':
      return 'Sigue sin espacio'
    default:
      return 'Sin cambios'
  }
}

/** US-B6 + US-C6: qué cambió en la agenda tras terminar una tarea, y por qué. */
export default function Replanificacion({ ir, replanificacion }) {
  if (!replanificacion) {
    return (
      <section className="estrecha">
        <Encabezado titulo="Replanificación" />
        <Vacio
          titulo="Todavía no hay replanificaciones."
          accion={
            <button type="button" className="boton" onClick={() => ir('hoy')}>
              Ir a Hoy
            </button>
          }
        >
          Cuando termines una tarea desde Hoy, FocusFlow rehace el resto de tu agenda y aquí te explica
          qué movió y por qué.
        </Vacio>
      </section>
    )
  }

  const { tarea, estimadoMin, realMin, antes, despues, nombres, momento } = replanificacion
  const filas = compararAgendas(antes, despues, tarea.id)
  const cambiaron = filas.filter((f) => f.cambio !== 'igual')
  const diferencia = realMin - estimadoMin
  const nombre = (id) => nombres[id] ?? `Tarea #${id}`

  return (
    <section>
      <Encabezado
        titulo="Replanificación"
        acciones={
          <button type="button" className="boton" onClick={() => ir('agenda')}>
            Ver agenda completa
          </button>
        }
      >
        Terminaste «{tarea.nombre}» ({etiquetaTipo(tarea.tipo).toLowerCase()}) · {fmtFechaHora(desdeIso(momento))}
      </Encabezado>

      <div className="resumen">
        <div className="dato">
          <span className="dato-valor">{fmtDuracion(estimadoMin)}</span>
          <span className="dato-etiqueta">estimaste</span>
        </div>
        <div className={`dato ${diferencia > 0 ? 'dato-alerta' : ''}`}>
          <span className="dato-valor">{fmtDuracion(realMin)}</span>
          <span className="dato-etiqueta">
            tomó en realidad
            {diferencia !== 0 && ` (${diferencia > 0 ? '+' : '−'}${fmtDuracion(diferencia)})`}
          </span>
        </div>
        <div className="dato">
          <span className="dato-valor">{cambiaron.length}</span>
          <span className="dato-etiqueta">
            {cambiaron.length === 1 ? 'tarea cambió' : 'tareas cambiaron'} en tu agenda
          </span>
        </div>
      </div>

      <blockquote className="explicacion">
        <p className="explicacion-etiqueta">Qué hizo FocusFlow</p>
        <p>{despues.explicacion || 'El agente no devolvió explicación.'}</p>
      </blockquote>

      {!despues.factible && (
        <div className="caja caja-alerta">
          <p>Con el tiempo libre que queda, no todas tus tareas caben.</p>
          <button type="button" className="enlace" onClick={() => ir('bloques')}>
            Agregar tiempo libre
          </button>
        </div>
      )}

      <h2>Antes y después</h2>
      <p className="ayuda">
        La explicación compara con lo que habría pasado si la tarea hubiera durado lo estimado. Esta
        tabla compara con la agenda que veías justo antes de terminarla.
      </p>
      {filas.length === 0 ? (
        <p className="ayuda">No quedan tareas pendientes.</p>
      ) : (
        <div className="tabla-contenedor">
          <table className="tabla">
            <thead>
              <tr>
                <th scope="col">Tarea</th>
                <th scope="col">Agenda que tenías</th>
                <th scope="col">Agenda nueva</th>
                <th scope="col">Cambio</th>
                <th scope="col">Riesgo</th>
              </tr>
            </thead>
            <tbody>
              {filas.map((f) => (
                <tr key={f.id} className={`cambio-${f.cambio}`}>
                  <th scope="row">{nombre(f.id)}</th>
                  <td>{f.antes ? fmtFechaHora(desdeIso(f.antes.inicio)) : '—'}</td>
                  <td>{f.despues ? fmtFechaHora(desdeIso(f.despues.inicio)) : '—'}</td>
                  <td>
                    <span className={`marca-cambio marca-${f.cambio}`}>{textoCambio(f)}</span>
                  </td>
                  <td>
                    {f.riesgoDespues !== undefined ? (
                      <Riesgo holgura={f.riesgoDespues} sinEspacio={!f.despues} />
                    ) : f.riesgoAntes !== undefined ? (
                      <span className="insignia riesgo-ok">Salió de riesgo</span>
                    ) : (
                      <span className="ayuda">A tiempo</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
