interface Props {
  activeAlerts: number;
  model: string;
  year?: string | number;
  status?: string;
}

export function SystemHealthCard({ activeAlerts, model, year = 2026, status = "ALL SYSTEMS OPTIMAL" }: Props) {
  const isHealthy = activeAlerts === 0;

  return (
    <div className="flex min-h-[210px] flex-col justify-between rounded-2xl bg-gradient-to-br from-[#5a1e29] via-[#7d2a3a] to-[#b23b4c] p-5 text-white shadow-sm sm:min-h-[230px] sm:p-6">
      <span className="text-[10px] uppercase tracking-widest text-white/70">System Health</span>
      <div>
        <div className="mb-2 text-[10px] uppercase tracking-widest text-white/70">Operational Mode</div>
        <div className="text-xl font-semibold sm:text-2xl">{status}</div>
        <div className="mt-2 text-sm text-white/75">
          {isHealthy
            ? "Zero active incidents detected across microservices."
            : `${activeAlerts} active alert${activeAlerts === 1 ? "" : "s"} require attention.`}
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 border-t border-white/15 pt-3 text-xs text-white/65">
        <span className="truncate font-mono">{model}</span>
        <span className="shrink-0 font-mono">/ {year}</span>
      </div>
    </div>
  );
}
