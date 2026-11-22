import { useCallback, useEffect, useState } from 'react'
import Hoy from './pantallas/Hoy.jsx'
import NuevaTarea from './pantallas/NuevaTarea.jsx'
import BloquesLibres from './pantallas/BloquesLibres.jsx'
import AgendaPropuesta from './pantallas/AgendaPropuesta.jsx'
import Replanificacion from './pantallas/Replanificacion.jsx'

// Las cinco pantallas del MVP (US-B5, US-B6). Navegación por #hash: sin
// dependencias extra y funciona igual en `vite` y en `vite preview`.
const PANTALLAS = [
  { ruta: 'hoy', titulo: 'Hoy', componente: Hoy },
  { ruta: 'nueva', titulo: 'Nueva tarea', componente: NuevaTarea },
  { ruta: 'bloques', titulo: 'Bloques libres', componente: BloquesLibres },
  { ruta: 'agenda', titulo: 'Agenda', componente: AgendaPropuesta },
  { ruta: 'replanificacion', titulo: 'Replanificación', componente: Replanificacion },
]

const rutaActual = () => {
  const ruta = window.location.hash.replace(/^#\/?/, '')
  return PANTALLAS.some((p) => p.ruta === ruta) ? ruta : 'hoy'
}

const CLAVE_REPLAN = 'focusflow:ultima-replanificacion'

function leerReplanificacion() {
  try {
    const guardada = sessionStorage.getItem(CLAVE_REPLAN)
    return guardada ? JSON.parse(guardada) : null
  } catch {
    return null
  }
}

export default function App() {
  const [ruta, setRuta] = useState(rutaActual)
  const [aviso, setAviso] = useState(null)
  // Lo que pasó en la última replanificación: lo produce "Hoy" al terminar una
  // tarea y lo muestra "Replanificación". Se guarda en sessionStorage para que
  // sobreviva a recargar la página durante la demo.
  const [replanificacion, setReplanificacionEstado] = useState(leerReplanificacion)

  useEffect(() => {
    const alCambiar = () => setRuta(rutaActual())
    window.addEventListener('hashchange', alCambiar)
    return () => window.removeEventListener('hashchange', alCambiar)
  }, [])

  useEffect(() => {
    if (!aviso) return undefined
    const t = setTimeout(() => setAviso(null), 4000)
    return () => clearTimeout(t)
  }, [aviso])

  const ir = useCallback((destino) => {
    window.location.hash = `/${destino}`
  }, [])

  const setReplanificacion = useCallback((datos) => {
    setReplanificacionEstado(datos)
    try {
      sessionStorage.setItem(CLAVE_REPLAN, JSON.stringify(datos))
    } catch {
      /* almacenamiento lleno o deshabilitado: no es crítico */
    }
  }, [])

  const Pantalla = PANTALLAS.find((p) => p.ruta === ruta).componente

  return (
    <div className="app">
      <header className="barra">
        <a className="marca" href="#/hoy" aria-label="FocusFlow, ir a Hoy">
          <img src="/icono.svg" alt="" width="28" height="28" />
          <span>FocusFlow</span>
        </a>
        <nav aria-label="Pantallas">
          <ul className="navegacion">
            {PANTALLAS.map((p) => (
              <li key={p.ruta}>
                <a
                  href={`#/${p.ruta}`}
                  aria-current={p.ruta === ruta ? 'page' : undefined}
                  className={p.ruta === ruta ? 'activa' : undefined}
                >
                  {p.titulo}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </header>

      <main className="contenido">
        <Pantalla
          ir={ir}
          avisar={setAviso}
          replanificacion={replanificacion}
          setReplanificacion={setReplanificacion}
        />
      </main>

      {aviso && (
        <div className="aviso" role="status">
          {aviso}
        </div>
      )}
    </div>
  )
}
