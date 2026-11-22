// Formatos y catálogos compartidos por las pantallas.

// Tipos de tarea del documento de contexto. Se envían tal cual al backend: el
// estimador aprende por tipo, así que todos deben escribirse igual (US-C1).
export const TIPOS = [
  { valor: 'programacion', etiqueta: 'Programación' },
  { valor: 'lectura', etiqueta: 'Lectura' },
  { valor: 'taller', etiqueta: 'Taller' },
  { valor: 'estudio_parcial', etiqueta: 'Estudio para parcial' },
]

export const etiquetaTipo = (valor) =>
  TIPOS.find((t) => t.valor === valor)?.etiqueta ?? valor

// Prioridad: mayor número = más importante (acordado para el contrato).
export const PRIORIDADES = [
  { valor: 1, etiqueta: 'Baja' },
  { valor: 2, etiqueta: 'Media' },
  { valor: 3, etiqueta: 'Alta' },
]

export const etiquetaPrioridad = (valor) =>
  PRIORIDADES.find((p) => p.valor === valor)?.etiqueta ?? `P${valor}`

export const ESTADOS = {
  pendiente: 'Pendiente',
  en_progreso: 'En progreso',
  terminada: 'Terminada',
}

// ---------- Fechas ----------

const dos = (n) => String(n).padStart(2, '0')

/** Date -> "2026-11-23T18:00" en hora local (lo que espera el backend). */
export function aIsoLocal(fecha) {
  const d = new Date(fecha)
  return `${d.getFullYear()}-${dos(d.getMonth() + 1)}-${dos(d.getDate())}T${dos(d.getHours())}:${dos(d.getMinutes())}`
}

/** "2026-11-23T18:00:00" (hora local, sin zona) -> Date. */
export function desdeIso(texto) {
  return new Date(texto)
}

/** Date -> "2026-11-23". */
export function claveDia(fecha) {
  const d = new Date(fecha)
  return `${d.getFullYear()}-${dos(d.getMonth() + 1)}-${dos(d.getDate())}`
}

/** "2026-11-23" -> Date a medianoche local. */
export function diaDesdeClave(clave) {
  const [a, m, d] = clave.split('-').map(Number)
  return new Date(a, m - 1, d)
}

export function sumarDias(fecha, dias) {
  const d = new Date(fecha)
  d.setDate(d.getDate() + dias)
  return d
}

export const esMismoDia = (a, b) => claveDia(a) === claveDia(b)

export function fmtHora(fecha) {
  return new Date(fecha).toLocaleTimeString('es-CO', { hour: 'numeric', minute: '2-digit' })
}

export function fmtDia(fecha, { conAnio = false, corto = false } = {}) {
  const d = new Date(fecha)
  const hoy = new Date()
  if (esMismoDia(d, hoy)) return 'Hoy'
  if (esMismoDia(d, sumarDias(hoy, 1))) return 'Mañana'
  if (esMismoDia(d, sumarDias(hoy, -1))) return 'Ayer'
  const texto = d.toLocaleDateString('es-CO', {
    weekday: corto ? 'short' : 'long',
    day: 'numeric',
    ...(corto ? {} : { month: 'short' }),
    ...(conAnio ? { year: 'numeric' } : {}),
  })
  return texto.charAt(0).toUpperCase() + texto.slice(1)
}

export function fmtFechaHora(fecha) {
  return `${fmtDia(fecha)}, ${fmtHora(fecha)}`
}

export function fmtDuracion(minutos) {
  const m = Math.abs(Math.round(minutos))
  const h = Math.floor(m / 60)
  const r = m % 60
  if (h && r) return `${h} h ${r} min`
  if (h) return `${h} h`
  return `${r} min`
}

export const minutosEntre = (a, b) => Math.round((new Date(b) - new Date(a)) / 60000)

/** Texto corto para la holgura de una tarea en riesgo. */
export function textoHolgura(holguraMin) {
  if (holguraMin < 0) return `${fmtDuracion(holguraMin)} tarde`
  if (holguraMin === 0) return 'sin margen'
  return `${fmtDuracion(holguraMin)} de margen`
}
