import { useEffect, useState } from 'react'
import { crearTarea, estimar } from '../api.js'
import { CajaError, Confianza, Encabezado } from '../componentes.jsx'
import { PRIORIDADES, TIPOS, aIsoLocal, fmtDuracion, sumarDias } from '../formato.js'

const DURACIONES_RAPIDAS = [30, 60, 90, 120, 180]

function limitePorDefecto() {
  const d = sumarDias(new Date(), 1)
  d.setHours(23, 59, 0, 0)
  return aIsoLocal(d)
}

/** US-B5 + US-C2: registrar una tarea (nombre, tipo, límite, prioridad, estimación). */
export default function NuevaTarea({ ir, avisar }) {
  const [nombre, setNombre] = useState('')
  const [tipo, setTipo] = useState(TIPOS[0].valor)
  const [limite, setLimite] = useState(limitePorDefecto)
  const [prioridad, setPrioridad] = useState(2)
  const [estimacion, setEstimacion] = useState(60)
  const [prediccion, setPrediccion] = useState(null)
  const [error, setError] = useState(null)
  const [enviando, setEnviando] = useState(false)

  // Mientras el usuario escribe, preguntarle al estimador qué cree él (US-A3/A7).
  useEffect(() => {
    if (!(estimacion > 0)) {
      setPrediccion(null)
      return undefined
    }
    let vigente = true
    const t = setTimeout(async () => {
      try {
        const p = await estimar(tipo, Math.round(estimacion))
        if (vigente) setPrediccion(p)
      } catch {
        if (vigente) setPrediccion(null)
      }
    }, 300)
    return () => {
      vigente = false
      clearTimeout(t)
    }
  }, [tipo, estimacion])

  const limiteEnPasado = limite && new Date(limite) < new Date()
  const valido = nombre.trim() && limite && estimacion > 0

  async function enviar(evento) {
    evento.preventDefault()
    if (!valido) return
    setEnviando(true)
    setError(null)
    try {
      const creada = await crearTarea({
        nombre: nombre.trim(),
        tipo,
        limite,
        prioridad,
        estimacion_usuario_min: Math.round(estimacion),
      })
      avisar(`"${creada.nombre}" quedó registrada. Así queda tu agenda.`)
      ir('agenda')
    } catch (e) {
      setError(e)
      setEnviando(false)
    }
  }

  const diferencia = prediccion ? prediccion.duracion_esperada_min - Math.round(estimacion) : 0

  return (
    <section className="estrecha">
      <Encabezado titulo="Nueva tarea">
        FocusFlow la ubica en tu tiempo libre usando lo que ha aprendido de tareas parecidas.
      </Encabezado>

      <form className="formulario" onSubmit={enviar} noValidate>
        <label className="campo">
          <span>Nombre</span>
          <input
            type="text"
            value={nombre}
            onChange={(e) => setNombre(e.target.value)}
            placeholder="Ej.: Taller 3 de Cálculo"
            maxLength={120}
            required
            autoFocus
          />
        </label>

        <fieldset className="campo">
          <legend>Tipo</legend>
          <div className="segmentado">
            {TIPOS.map((t) => (
              <label key={t.valor} className={tipo === t.valor ? 'elegido' : undefined}>
                <input
                  type="radio"
                  name="tipo"
                  value={t.valor}
                  checked={tipo === t.valor}
                  onChange={() => setTipo(t.valor)}
                />
                {t.etiqueta}
              </label>
            ))}
          </div>
        </fieldset>

        <div className="fila-campos">
          <label className="campo">
            <span>Fecha límite</span>
            <input type="datetime-local" value={limite} onChange={(e) => setLimite(e.target.value)} required />
            {limiteEnPasado && <span className="ayuda texto-alerta">Esa fecha ya pasó: la tarea nacerá atrasada.</span>}
          </label>

          <fieldset className="campo">
            <legend>Prioridad</legend>
            <div className="segmentado">
              {PRIORIDADES.map((p) => (
                <label key={p.valor} className={prioridad === p.valor ? 'elegido' : undefined}>
                  <input
                    type="radio"
                    name="prioridad"
                    value={p.valor}
                    checked={prioridad === p.valor}
                    onChange={() => setPrioridad(p.valor)}
                  />
                  {p.etiqueta}
                </label>
              ))}
            </div>
          </fieldset>
        </div>

        <fieldset className="campo">
          <legend>¿Cuánto crees que te va a tomar?</legend>
          <div className="fila-estimacion">
            <input
              type="number"
              min="5"
              step="5"
              value={estimacion}
              onChange={(e) => setEstimacion(e.target.valueAsNumber)}
              aria-label="Estimación en minutos"
              required
            />
            <span>minutos</span>
            <div className="chips">
              {DURACIONES_RAPIDAS.map((m) => (
                <button
                  key={m}
                  type="button"
                  className={`chip ${estimacion === m ? 'elegido' : ''}`}
                  onClick={() => setEstimacion(m)}
                >
                  {fmtDuracion(m)}
                </button>
              ))}
            </div>
          </div>
        </fieldset>

        {prediccion && (
          <div className="caja caja-prediccion" aria-live="polite">
            <p>
              {prediccion.n_observaciones === 0 ? (
                <>Aún no hay historial de este tipo: FocusFlow usará tu estimación y la irá corrigiendo.</>
              ) : (
                <>
                  Según tus {prediccion.n_observaciones} tarea{prediccion.n_observaciones === 1 ? '' : 's'} de
                  este tipo, FocusFlow reservará <strong>{fmtDuracion(prediccion.duracion_esperada_min)}</strong>
                  {diferencia > 0 && <> ({fmtDuracion(diferencia)} más de lo que estimas)</>}
                  {diferencia < 0 && <> ({fmtDuracion(-diferencia)} menos de lo que estimas)</>}.
                </>
              )}
            </p>
            <Confianza estimacion={prediccion} />
          </div>
        )}

        <CajaError error={error} />

        <div className="acciones">
          <button type="submit" className="boton" disabled={!valido || enviando}>
            {enviando ? 'Guardando…' : 'Registrar tarea'}
          </button>
          <button type="button" className="boton fantasma" onClick={() => ir('hoy')}>
            Cancelar
          </button>
        </div>
      </form>
    </section>
  )
}
