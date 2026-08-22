/**
 * The product's ethical guarantee, rendered as a component so it is physically
 * impossible to ship a screen or a generated message that quietly drops it.
 * Every surface that shows emergency information must render this.
 */
export function Disclaimer({
  lang = "es",
  compact = false,
}: {
  lang?: string;
  compact?: boolean;
}) {
  const es = !lang.startsWith("en");
  const line1 = es
    ? "Esta herramienta informa, no da instrucciones."
    : "This tool informs, it does not instruct.";
  const line2 = es
    ? "Nunca genera ni sugiere procedimientos médicos o de rescate, y no reemplaza a los servicios de emergencia profesionales."
    : "It never generates or suggests medical or rescue procedures, and does not replace professional emergency services.";
  const line3 = es
    ? "Si hay peligro inmediato, llame al 123 (Colombia), 911 o 112."
    : "If there is immediate danger, call 123 (Colombia), 911 or 112.";

  if (compact) {
    return (
      <p className="text-[11px] leading-relaxed text-ink-400">
        {line1} {line3}
      </p>
    );
  }
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-900 p-3 text-xs leading-relaxed text-ink-200">
      <p className="font-semibold text-ink-50">{line1}</p>
      <p className="mt-1 text-ink-400">{line2}</p>
      <p className="mt-1 text-ink-400">{line3}</p>
    </div>
  );
}
