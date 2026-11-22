import { useCallback, useEffect, useMemo, useState } from 'react'
import { guardarBloquesDia, listarBloques } from '../api.js'
import { CajaError, Cargando, Encabezado } from '../componentes.jsx'
import { claveDia, desdeIso, diaDesdeClave, fmtDia, fmtDuracion, minutosEntre, sumarDias } from '../formato.js'

const PLANTILLAS = [
  { etiqueta: 'Mañana 8–12', inicio: '08:00', fin: '12:00' },
  { etiqueta: 'Tarde 14–18', inicio: '14:00', fin: '18:00' },
  { etiqueta: 'Noche 19–22', inicio: '19:00', fin: '22:00' },
]

const aMinutos = (hhmm) => {
  const [h, m] = hhmm.split(':').map(Number)
  return h * 60 + m
}
const hhmm = (fecha) => {
  const d = new Date(fecha)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** Valida los rangos de un día igual que el backend: fin > inicio y sin solapes. */
function validar(rangos) {
  for (const r of rangos) {
    if (!r.inicio || !r.fin) return 'Completa la hora de inicio y fin de cada bloque.'
    if (aMinutos(r.fin) <= aMinutos(r.inicio)) return `El bloque ${r.inicio}–${r.fin} termina antes de empezar.`
  }
  const ordenados = [...rangos].sort((a, b) => aMinutos(a.inicio) - aMinutos(b.inicio))
  for (let i = 1; i < ordenados.length; i++) {
    if (aMinutos(ordenados[i].inicio) < aMinutos(ordenados[i - 1].fin)) {
      return `Los bloques ${ordenados[i - 1].inicio}–${ordenados[i - 1].fin} y ${ordenados[i].inicio}–${ordenados[i].fin} se solapan.`
    }
  }
  return null
}

/** US-B6 + US-C3: declarar el tiempo libre de cada día. */
export default function BloquesLibres({ avisar }) {
  const [dia, setDia] = useState(() => claveDia(new Date()))
  const [todos, setTodos] = useState(null)
  const [rangos, setRangos] = useState([])
  const [sucio, setSucio] = useState(false)
  const [error, setError] = useState(null)
  const [guardando, setGuardando] = useState(false)

  const cargar = useCallback(async () => {
    setError(null)
    try {
      setTodos(await listarBloques())
    } catch (e) {
      setError(e)
    }
  }, [])

  useEffect(() => {
    cargar()
  }, [cargar])

  const porDia = useMemo(() => {
    const mapa = new Map()
    for (const b of todos ?? []) {
      const clave = claveDia(desdeIso(b.inicio))
      if (!mapa.has(clave)) mapa.set(clave, [])
      mapa.get(clave).push(b)
    }
    return mapa
  }, [todos])

  // Al cambiar de día (o al cargar), mostrar lo que ya hay guardado.
  useEffect(() => {
    if (!todos) return
    setRangos((porDia.get(dia) ?? []).map((b) => ({ inicio: hhmm(b.inicio), fin: hhmm(b.fin) })))
    setSucio(false)
  }, [dia, todos, porDia])

  const semana = Array.from({ length: 7 }, (_, i) => claveDia(sumarDias(new Date(), i)))
  const problema = validar(rangos)
  const totalDia = rangos.reduce(
    (s, r) => s + (r.inicio && r.fin ? Math.max(0, aMinutos(r.fin) - aMinutos(r.inicio)) : 0),
    0,
  )

  const cambiar = (indice, campo, valor) => {
    setRangos((rs) => rs.map((r, i) => (i === indice ? { ...r, [campo]: valor } : r)))
    setSucio(true)
  }
  const agregar = (rango = { inicio: '18:00', fin: '20:00' }) => {
    setRangos((rs) => [...rs, rango])
    setSucio(true)
  }
  const quitar = (indice) => {
    setRangos((rs) => rs.filter((_, i) => i !== indice))
    setSucio(true)
  }

  async function guardar() {
    if (problema) return
    setGuardando(true)
    setError(null)
    try {
      await guardarBloquesDia(
        dia,
        [...rangos].sort((a, b) => aMinutos(a.inicio) - aMinutos(b.inicio)),
      )
      await cargar()
      avisar(`Tiempo libre de ${fmtDia(diaDesdeClave(dia)).toLowerCase()} guardado.`)
    } catch (e) {
      setError(e)
    } finally {
      setGuardando(false)
    }
  }

  if (!todos && !error) return <Cargando />

  return (
    <section>
      <Encabezado titulo="Bloques libres">
        Indica cuándo puedes trabajar. Guardar un día reemplaza los bloques que ese día ya tenía.
      </Encabezado>

      <div className="semana" role="tablist" aria-label="Día">
        {semana.map((clave) => {
          const bloques = porDia.get(clave) ?? []
          const minutos = bloques.reduce((s, b) => s + minutosEntre(b.inicio, b.fin), 0)
          return (
            <button
              key={clave}
              type="button"
              role="tab"
              aria-selected={clave === dia}
              className={`dia ${clave === dia ? 'elegido' : ''}`}
              onClick={() => setDia(clave)}
            >
              <span className="dia-nombre">{fmtDia(diaDesdeClave(clave), { corto: true })}</span>
              <span className="dia-total">{minutos ? fmtDuracion(minutos) : 'Sin bloques'}</span>
            </button>
          )
        })}
        <label className="dia dia-otro">
          <span className="dia-nombre">Otro día</span>
          <input type="date" value={dia} onChange={(e) => e.target.value && setDia(e.target.value)} />
        </label>
      </div>

      <div className="panel">
        <h2>
          {fmtDia(diaDesdeClave(dia), { conAnio: false })}
          <span className="subtitulo-h2"> · {totalDia ? `${fmtDuracion(totalDia)} libres` : 'sin tiempo libre'}</span>
        </h2>

        {rangos.length === 0 && <p className="ayuda">No hay bloques para este día.</p>}
        <ul className="rangos">
          {rangos.map((r, i) => (
            <li key={i} className="rango">
              <label>
                Desde
                <input type="time" value={r.inicio} onChange={(e) => cambiar(i, 'inicio', e.target.value)} />
              </label>
              <label>
                Hasta
                <input type="time" value={r.fin} onChange={(e) => cambiar(i, 'fin', e.target.value)} />
              </label>
              <button type="button" className="boton fantasma" onClick={() => quitar(i)} aria-label={`Quitar bloque ${r.inicio}–${r.fin}`}>
                Quitar
              </button>
            </li>
          ))}
        </ul>

        <div className="chips">
          <button type="button" className="chip" onClick={() => agregar()}>
            + Bloque
          </button>
          {PLANTILLAS.map((p) => (
            <button key={p.etiqueta} type="button" className="chip" onClick={() => agregar({ inicio: p.inicio, fin: p.fin })}>
              + {p.etiqueta}
            </button>
          ))}
        </div>

        {problema && <p className="ayuda texto-error">{problema}</p>}
        <CajaError error={error} />

        <div className="acciones">
          <button type="button" className="boton" onClick={guardar} disabled={!!problema || guardando || !sucio}>
            {guardando ? 'Guardando…' : sucio ? 'Guardar este día' : 'Guardado'}
          </button>
        </div>
      </div>
    </section>
  )
}
