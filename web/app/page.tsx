import Link from "next/link";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-8 px-6 py-16">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-crit-500">
          Platanus Hack 26 · Emergencias
        </p>
        <h1 className="mt-3 text-4xl font-bold tracking-tight text-ink-50">
          Emergency Language Bridge
        </h1>
        <p className="mt-4 text-lg leading-relaxed text-ink-200">
          Interpretación en vivo, en las dos direcciones, entre una persona que no habla
          español y el operador del 123. Al cerrar la llamada genera un reporte
          estructurado y lo entrega a la red de apoyo de la persona afectada.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Link
          href="/operator"
          className="group rounded-xl border border-ink-700 bg-ink-900 p-5 transition hover:border-crit-600 hover:bg-ink-850"
        >
          <div className="text-sm font-semibold text-crit-500">Consola del operador</div>
          <p className="mt-1 text-sm text-ink-400">
            Puesto del despachador: transcripción bilingüe, términos críticos resaltados
            y ficha de despacho en vivo.
          </p>
          <span className="mt-3 inline-block text-sm text-ink-200 group-hover:text-ink-50">
            Abrir consola →
          </span>
        </Link>

        <div className="rounded-xl border border-ink-700 bg-ink-900 p-5">
          <div className="text-sm font-semibold text-info-400">App del llamante</div>
          <p className="mt-1 text-sm text-ink-400">
            Android (Kotlin + Compose). Instala el APK, elige tu idioma, configura tus
            contactos de confianza y pulsa el botón de emergencia.
          </p>
          <span className="mt-3 inline-block text-sm text-ink-400">
            Ver RUNBOOK.md para compilar el APK
          </span>
        </div>
      </div>

    </main>
  );
}
