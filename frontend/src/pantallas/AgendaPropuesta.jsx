import { CajaError, Cargando, Encabezado, Vacio } from '../componentes.jsx'
import TarjetaTarea from '../TarjetaTarea.jsx'
import { claveDia, diaDesdeClave, fmtDia, fmtDuracion, fmtHora, minutosEntre } from '../formato.js'
import { useAgenda } from '../useAgenda.js'

/** US-B6 + US-B4: la agenda que propone el planificador, con el riesgo marcado. */
export default function AgendaPropuesta({ ir }) {
  const { datos, indices, error, cargando, recargar } = useAgenda()

  if (cargando && !datos) return <Cargando texto="El planificador está armando tu agenda…" />
  if (error) return <CajaError error={error} alReintentar={recargar} />

  const { tareaPorId, riesgoPorId, sinEspacio, agendaOrdenada } = indices
  const porDia = new Map()
  for (const item of agendaOrdenada) {
    const clave = claveDia(item.inicioFecha)
    if (!porDia.has(clave)) porDia.set(clave, [])
    porDia.get(clave).push(item)
  }
  const enRiesgo = datos.plan.en_riesgo
  const atrasadas = enRiesgo.filter((r) => r.holgura_min < 0).length

  return (
    <section>
      <Encabezado
        titulo="Agenda propuesta"
        acciones={
          <button type="button" className="boton secundario" onClick={recargar} disabled={cargando}>
            {cargando ? 'Recalculando…' : 'Recalcular'}
          </button>
        }
      >
        Calculada a las {fmtHora(datos.calculadaEn)} con tus bloques libres desde ahora. Cada tarea va
        entera en un bloque, con 15 min de descanso entre tareas.
      </Encabezado>

      {datos.pendientes.length === 0 ? (
        <Vacio
          titulo="No hay tareas pendientes."
          accion={
            <button type="button" className="boton" onClick={() => ir('nueva')}>
              Registrar una tarea
            </button>
          }
        />
      ) : (
        <>
          <div
            className={`caja ${datos.plan.factible && !enRiesgo.length ? 'caja-ok' : 'caja-alerta'}`}
            role="status"
          >
            {datos.plan.factible ? (
              enRiesgo.length ? (
                <p>
                  Todas tus tareas caben, pero <strong>{enRiesgo.length}</strong> está
                  {enRiesgo.length === 1 ? '' : 'n'} en riesgo
                  {atrasadas ? ` (${atrasadas} no alcanza${atrasadas === 1 ? '' : 'n'} su plazo)` : ''}.
                </p>
              ) : (
                <p>Todas tus tareas caben y llegan a tiempo.</p>
              )
            ) : (
              <p>
                <strong>{sinEspacio.length}</strong> tarea{sinEspacio.length === 1 ? '' : 's'} no cabe
                {sinEspacio.length === 1 ? '' : 'n'} en tu tiempo libre. Declara más bloques o ajusta los
                plazos.{' '}
                <button type="button" className="enlace" onClick={() => ir('bloques')}>
                  Agregar tiempo libre
                </button>
              </p>
            )}
          </div>

          {[...porDia.entries()].map(([clave, items]) => {
            const minutos = items.reduce((s, a) => s + minutosEntre(a.inicioFecha, a.finFecha), 0)
            return (
              <div key={clave} className="grupo-dia">
                <h2>
                  {fmtDia(diaDesdeClave(clave))}
                  <span className="subtitulo-h2">
                    {' '}
                    · {items.length} tarea{items.length === 1 ? '' : 's'}, {fmtDuracion(minutos)}
                  </span>
                </h2>
                <div className="lista linea-tiempo">
                  {items.map((item) => (
                    <TarjetaTarea
                      key={item.tarea_id}
                      tarea={tareaPorId.get(item.tarea_id)}
                      item={item}
                      estimacion={datos.estimaciones.get(item.tarea_id)}
                      holgura={riesgoPorId.get(item.tarea_id)}
                    />
                  ))}
                </div>
              </div>
            )
          })}

          {sinEspacio.length > 0 && (
            <div className="grupo-dia">
              <h2>Sin espacio en la agenda</h2>
              <p className="ayuda">
                O ningún bloque es lo bastante largo (una tarea no se parte entre bloques), o el tiempo
                libre ya está ocupado por tareas más urgentes o importantes.
              </p>
              <div className="lista">
                {sinEspacio.map((t) => (
                  <TarjetaTarea
                    key={t.id}
                    tarea={t}
                    estimacion={datos.estimaciones.get(t.id)}
                    holgura={riesgoPorId.get(t.id)}
                    sinEspacio
                  />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  )
}
