import { useState } from 'react'
import { editarTarea, terminarTarea } from '../api.js'
import { CajaError, Cargando, Encabezado, Vacio } from '../componentes.jsx'
import TarjetaTarea from '../TarjetaTarea.jsx'
import { aIsoLocal, desdeIso, esMismoDia, fmtDuracion, minutosEntre } from '../formato.js'
import { useAgenda } from '../useAgenda.js'

// Hora real de inicio, guardada al pulsar "Empezar" (el backend solo recibe
// inicio y fin juntos, al terminar).
const claveInicio = (id) => `focusflow:inicio:${id}`

/** US-B5: el día del usuario, con el estado de cada tarea. */
export default function Hoy({ ir, avisar, setReplanificacion }) {
  const { datos, indices, error, cargando, recargar } = useAgenda()
  const [terminando, setTerminando] = useState(null) // id de la tarea con el formulario abierto
  const [errorAccion, setErrorAccion] = useState(null)

  if (cargando && !datos) return <Cargando texto="Armando tu agenda…" />
  if (error) return <CajaError error={error} alReintentar={recargar} />

  const hoy = new Date()
  const { tareaPorId, riesgoPorId, sinEspacio, agendaOrdenada } = indices
  const deHoy = agendaOrdenada.filter((a) => esMismoDia(a.inicioFecha, hoy))
  const enCurso = datos.tareas.filter((t) => t.estado === 'en_progreso')
  const idsVisibles = new Set([...enCurso.map((t) => t.id), ...deHoy.map((a) => a.tarea_id)])
  const enRiesgo = datos.plan.en_riesgo.length
  const despues = agendaOrdenada.length - deHoy.length
  const minutosHoy = deHoy.reduce((s, a) => s + minutosEntre(a.inicioFecha, a.finFecha), 0)

  async function empezar(tarea) {
    setErrorAccion(null)
    try {
      localStorage.setItem(claveInicio(tarea.id), aIsoLocal(new Date()))
      await editarTarea(tarea.id, { estado: 'en_progreso' })
      avisar(`Empezaste "${tarea.nombre}".`)
      recargar()
    } catch (e) {
      setErrorAccion(e)
    }
  }

  async function terminar(tarea, inicio, fin) {
    setErrorAccion(null)
    const nombres = Object.fromEntries(datos.tareas.map((t) => [t.id, t.nombre]))
    try {
      const resultado = await terminarTarea(tarea.id, inicio, fin)
      localStorage.removeItem(claveInicio(tarea.id))
      setReplanificacion({
        momento: aIsoLocal(new Date()),
        tarea: { id: tarea.id, nombre: tarea.nombre, tipo: tarea.tipo },
        estimadoMin: tarea.estimacion_usuario_min,
        realMin: minutosEntre(inicio, fin),
        antes: datos.plan,
        despues: resultado,
        nombres,
      })
      ir('replanificacion')
    } catch (e) {
      setErrorAccion(e)
    }
  }

  const tarjeta = (tarea, item) => (
    <TarjetaTarea
      key={tarea.id}
      tarea={tarea}
      item={item}
      estimacion={datos.estimaciones.get(tarea.id)}
      holgura={riesgoPorId.get(tarea.id)}
      sinEspacio={!item && tarea.estado !== 'terminada'}
    >
      <div className="acciones">
        {tarea.estado === 'pendiente' && (
          <button type="button" className="boton secundario" onClick={() => empezar(tarea)}>
            Empezar
          </button>
        )}
        <button
          type="button"
          className="boton"
          aria-expanded={terminando === tarea.id}
          onClick={() => setTerminando(terminando === tarea.id ? null : tarea.id)}
        >
          Terminar
        </button>
      </div>
      {terminando === tarea.id && (
        <FormularioTerminar
          tarea={tarea}
          item={item}
          alCancelar={() => setTerminando(null)}
          alTerminar={terminar}
        />
      )}
    </TarjetaTarea>
  )

  return (
    <section>
      <Encabezado
        titulo="Hoy"
        acciones={
          <button type="button" className="boton secundario" onClick={recargar} disabled={cargando}>
            {cargando ? 'Actualizando…' : 'Actualizar'}
          </button>
        }
      >
        {hoy.toLocaleDateString('es-CO', { weekday: 'long', day: 'numeric', month: 'long' })}
      </Encabezado>

      <div className="resumen">
        <div className="dato">
          <span className="dato-valor">{deHoy.length}</span>
          <span className="dato-etiqueta">tareas en tu agenda de hoy</span>
        </div>
        <div className="dato">
          <span className="dato-valor">{fmtDuracion(minutosHoy)}</span>
          <span className="dato-etiqueta">de trabajo planeado</span>
        </div>
        <div className={`dato ${enRiesgo ? 'dato-alerta' : ''}`}>
          <span className="dato-valor">{enRiesgo}</span>
          <span className="dato-etiqueta">en riesgo de no cumplir su plazo</span>
        </div>
      </div>

      <CajaError error={errorAccion} />

      {enCurso.length > 0 && (
        <>
          <h2>En curso</h2>
          <div className="lista">
            {enCurso.map((t) => tarjeta(t, indices.itemPorId.get(t.id)))}
          </div>
        </>
      )}

      <h2>Tu agenda de hoy</h2>
      {deHoy.filter((a) => !enCurso.some((t) => t.id === a.tarea_id)).length ? (
        <div className="lista">
          {deHoy
            .filter((a) => !enCurso.some((t) => t.id === a.tarea_id))
            .map((a) => tarjeta(tareaPorId.get(a.tarea_id), a))}
        </div>
      ) : (
        <Vacio
          titulo={datos.pendientes.length ? 'No queda nada agendado para hoy.' : 'No tienes tareas pendientes.'}
          accion={
            <div className="acciones">
              <button type="button" className="boton" onClick={() => ir('nueva')}>
                Registrar una tarea
              </button>
              <button type="button" className="boton secundario" onClick={() => ir('bloques')}>
                Declarar tiempo libre
              </button>
            </div>
          }
        >
          {datos.pendientes.length
            ? 'Si quieres avanzar hoy, declara un bloque libre para lo que queda del día.'
            : 'Registra lo que tienes que hacer y FocusFlow arma la agenda.'}
        </Vacio>
      )}

      {(despues > 0 || sinEspacio.some((t) => !idsVisibles.has(t.id))) && (
        <div className="caja caja-info">
          {despues > 0 && <p>{despues} tarea{despues === 1 ? '' : 's'} más agendada{despues === 1 ? '' : 's'} para los próximos días.</p>}
          {sinEspacio.length > 0 && (
            <p>
              <strong>{sinEspacio.length}</strong> tarea{sinEspacio.length === 1 ? '' : 's'} no cabe
              {sinEspacio.length === 1 ? '' : 'n'} en tu tiempo libre declarado.
            </p>
          )}
          <button type="button" className="boton secundario" onClick={() => ir('agenda')}>
            Ver la agenda completa
          </button>
        </div>
      )}
    </section>
  )
}

/** Horas reales de inicio y fin (US-C4). Editables, para registrar lo que pasó
 * de verdad o para simular un atraso en la demo. */
function FormularioTerminar({ tarea, item, alCancelar, alTerminar }) {
  const guardado = localStorage.getItem(claveInicio(tarea.id))
  const ahora = new Date()
  const inicioSugerido = guardado
    ? desdeIso(guardado)
    : item && item.inicioFecha < ahora
      ? item.inicioFecha
      : new Date(ahora.getTime() - tarea.estimacion_usuario_min * 60000)

  const [inicio, setInicio] = useState(aIsoLocal(inicioSugerido))
  const [fin, setFin] = useState(aIsoLocal(ahora))
  const [enviando, setEnviando] = useState(false)

  const duracion = minutosEntre(inicio, fin)
  const valido = duracion > 0

  async function enviar(evento) {
    evento.preventDefault()
    if (!valido) return
    setEnviando(true)
    await alTerminar(tarea, desdeIso(inicio), desdeIso(fin))
    setEnviando(false)
  }

  return (
    <form className="formulario-terminar" onSubmit={enviar}>
      <div className="fila-campos">
        <label>
          Empezaste
          <input type="datetime-local" value={inicio} onChange={(e) => setInicio(e.target.value)} required />
        </label>
        <label>
          Terminaste
          <input type="datetime-local" value={fin} onChange={(e) => setFin(e.target.value)} required />
        </label>
      </div>
      <p className={`ayuda ${valido ? '' : 'texto-error'}`}>
        {valido
          ? `Duración real: ${fmtDuracion(duracion)} (estimaste ${fmtDuracion(tarea.estimacion_usuario_min)}).`
          : 'La hora de fin debe ser posterior a la de inicio.'}
      </p>
      <div className="acciones">
        <button type="submit" className="boton" disabled={!valido || enviando}>
          {enviando ? 'Replanificando…' : 'Registrar y replanificar'}
        </button>
        <button type="button" className="boton fantasma" onClick={alCancelar}>
          Cancelar
        </button>
      </div>
    </form>
  )
}
