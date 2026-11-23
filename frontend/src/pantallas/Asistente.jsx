import { useEffect, useRef, useState } from 'react'
import { aplicarPropuesta, conversarAsistente, estadoAsistente } from '../api.js'
import { CajaError, Cargando, Encabezado } from '../componentes.jsx'

const CLAVE_CHAT = 'focusflow:chat'

const SUGERENCIAS = [
  '¿Me alcanza el tiempo para todo esta semana?',
  '¿Qué me toca hacer hoy?',
  '¿En qué tipo de tarea me equivoco más al estimar?',
  'Tengo un quiz de inglés el viernes, repasar 1 hora',
]

const TITULO_ACCION = {
  crear_tarea: 'Crear tarea',
  editar_tarea: 'Editar tarea',
  bloques_dia: 'Tiempo libre',
}

function leerChat() {
  try {
    return JSON.parse(sessionStorage.getItem(CLAVE_CHAT)) ?? []
  } catch {
    return []
  }
}

/** Texto del modelo con un markdown mínimo: párrafos, viñetas y **negrita**. */
function TextoModelo({ texto }) {
  const enLinea = (linea) =>
    linea.split(/(\*\*[^*]+\*\*)/g).map((parte, i) =>
      parte.startsWith('**') && parte.endsWith('**') ? <strong key={i}>{parte.slice(2, -2)}</strong> : parte,
    )
  const bloques = []
  let lista = null
  texto.split('\n').forEach((cruda, i) => {
    const linea = cruda.trim()
    const vineta = linea.match(/^[-*•]\s+(.*)/) || linea.match(/^\d+[.)]\s+(.*)/)
    if (vineta) {
      if (!lista) bloques.push((lista = { tipo: 'lista', items: [], clave: i }))
      lista.items.push(vineta[1])
    } else {
      lista = null
      if (linea) bloques.push({ tipo: 'p', texto: linea, clave: i })
    }
  })
  return bloques.map((b) =>
    b.tipo === 'lista' ? (
      <ul key={b.clave}>
        {b.items.map((item, j) => (
          <li key={j}>{enLinea(item)}</li>
        ))}
      </ul>
    ) : (
      <p key={b.clave}>{enLinea(b.texto)}</p>
    ),
  )
}

function TarjetaPropuesta({ propuesta, estado, onConfirmar, onDescartar }) {
  return (
    <div className={`propuesta propuesta-${estado}`}>
      <p className="propuesta-titulo">{TITULO_ACCION[propuesta.accion] ?? propuesta.accion}</p>
      <p>{propuesta.resumen}</p>
      {estado === 'pendiente' || estado === 'aplicando' ? (
        <div className="acciones">
          <button type="button" className="boton" onClick={onConfirmar} disabled={estado === 'aplicando'}>
            {estado === 'aplicando' ? 'Aplicando…' : 'Confirmar'}
          </button>
          <button type="button" className="boton fantasma" onClick={onDescartar} disabled={estado === 'aplicando'}>
            Descartar
          </button>
        </div>
      ) : (
        <p className={`propuesta-estado ${estado === 'error' ? 'texto-error' : ''}`}>
          {estado === 'aplicada' ? '✓ Aplicada' : estado === 'descartada' ? 'Descartada' : 'No se pudo aplicar'}
        </p>
      )}
    </div>
  )
}

/**
 * Chat con el agente (función 2). El modelo consulta tareas, tiempo libre,
 * agenda e historial con herramientas, y los cambios llegan como propuestas
 * que el usuario confirma: nada se modifica sin su clic.
 */
export default function Asistente({ ir, avisar }) {
  const [estado, setEstado] = useState(null)
  const [errorEstado, setErrorEstado] = useState(null)
  const [mensajes, setMensajes] = useState(leerChat)
  const [entrada, setEntrada] = useState('')
  const [pensando, setPensando] = useState(false)
  const [error, setError] = useState(null)
  const final = useRef(null)

  useEffect(() => {
    estadoAsistente().then(setEstado, setErrorEstado)
  }, [])

  useEffect(() => {
    try {
      sessionStorage.setItem(CLAVE_CHAT, JSON.stringify(mensajes))
    } catch {
      /* no es crítico */
    }
    final.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [mensajes, pensando])

  async function enviar(texto) {
    const limpio = texto.trim()
    if (!limpio || pensando) return
    const conPregunta = [...mensajes, { rol: 'usuario', texto: limpio }]
    setMensajes(conPregunta)
    setEntrada('')
    setPensando(true)
    setError(null)
    try {
      const r = await conversarAsistente(conPregunta.map(({ rol, texto }) => ({ rol, texto })))
      setMensajes((ms) => [
        ...ms,
        {
          rol: 'asistente',
          texto: r.respuesta,
          consultas: r.consultas,
          modelo: r.modelo,
          propuestas: r.propuestas.map((p) => ({ ...p, estado: 'pendiente' })),
        },
      ])
    } catch (e) {
      setError(e)
      setMensajes((ms) => ms.slice(0, -1)) // devolver la pregunta al cuadro para reintentar
      setEntrada(limpio)
    } finally {
      setPensando(false)
    }
  }

  function cambiarPropuesta(id, cambios) {
    setMensajes((ms) =>
      ms.map((m) => (m.propuestas ? { ...m, propuestas: m.propuestas.map((p) => (p.id === id ? { ...p, ...cambios } : p)) } : m)),
    )
  }

  async function confirmar(propuesta) {
    cambiarPropuesta(propuesta.id, { estado: 'aplicando' })
    try {
      await aplicarPropuesta(propuesta)
      cambiarPropuesta(propuesta.id, { estado: 'aplicada' })
      // El agente se entera en el siguiente mensaje de lo que el usuario aceptó.
      setMensajes((ms) => [...ms, { rol: 'usuario', nota: true, texto: `Confirmé: ${propuesta.resumen}` }])
      avisar('Listo. Tu agenda ya tiene en cuenta el cambio.')
    } catch (e) {
      cambiarPropuesta(propuesta.id, { estado: 'error' })
      setError(e)
    }
  }

  function descartar(propuesta) {
    cambiarPropuesta(propuesta.id, { estado: 'descartada' })
    setMensajes((ms) => [...ms, { rol: 'usuario', nota: true, texto: `Descarté: ${propuesta.resumen}` }])
  }

  if (!estado && !errorEstado) return <Cargando />

  const disponible = estado?.disponible
  return (
    <section className="estrecha chat">
      <Encabezado
        titulo="Asistente"
        acciones={
          mensajes.length > 0 && (
            <button type="button" className="boton fantasma" onClick={() => setMensajes([])} disabled={pensando}>
              Nueva conversación
            </button>
          )
        }
      >
        Pregúntale por tu semana o pídele cambios. Consulta tu agenda real y los cambios te los propone para que
        tú los confirmes.
      </Encabezado>

      <CajaError error={errorEstado} />
      {estado && !disponible && (
        <div className="caja caja-alerta">
          <p>
            <strong>El asistente no tiene un modelo de lenguaje disponible.</strong> {estado.mensaje}
          </p>
          <p className="ayuda">
            Inicia Ollama con el modelo local o configura una clave de Claude (ver docs/asistente.md). Mientras
            tanto puedes escribir tareas en lenguaje natural en{' '}
            <button type="button" className="enlace" onClick={() => ir('nueva')}>
              Nueva tarea
            </button>
            .
          </p>
        </div>
      )}

      <div className="conversacion" aria-live="polite">
        {mensajes.length === 0 && disponible && (
          <div className="sugerencias">
            <p className="ayuda">Prueba con:</p>
            <div className="chips">
              {SUGERENCIAS.map((s) => (
                <button key={s} type="button" className="chip" onClick={() => enviar(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {mensajes.map((m, i) =>
          m.nota ? (
            <p key={i} className="nota-chat">
              {m.texto}
            </p>
          ) : (
            <div key={i} className={`burbuja burbuja-${m.rol}`}>
              {m.rol === 'asistente' ? <TextoModelo texto={m.texto} /> : <p>{m.texto}</p>}
              {m.propuestas?.map((p) => (
                <TarjetaPropuesta
                  key={p.id}
                  propuesta={p}
                  estado={p.estado}
                  onConfirmar={() => confirmar(p)}
                  onDescartar={() => descartar(p)}
                />
              ))}
              {m.consultas?.length > 0 && (
                <p className="consultas">
                  Consultó {m.consultas.join(', ')}
                  {m.modelo && <> · {m.modelo}</>}
                </p>
              )}
            </div>
          ),
        )}

        {pensando && (
          <div className="burbuja burbuja-asistente pensando" aria-label="El asistente está pensando">
            <span className="punto" />
            <span className="punto" />
            <span className="punto" />
          </div>
        )}
        <div ref={final} />
      </div>

      <CajaError error={error} />

      <form
        className="entrada-chat"
        onSubmit={(e) => {
          e.preventDefault()
          enviar(entrada)
        }}
      >
        <textarea
          value={entrada}
          onChange={(e) => setEntrada(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              enviar(entrada)
            }
          }}
          rows={2}
          maxLength={2000}
          placeholder={disponible ? 'Escribe tu mensaje…' : 'El asistente no está disponible'}
          disabled={!disponible || pensando}
          aria-label="Mensaje para el asistente"
        />
        <button type="submit" className="boton" disabled={!disponible || pensando || !entrada.trim()}>
          Enviar
        </button>
      </form>
    </section>
  )
}
