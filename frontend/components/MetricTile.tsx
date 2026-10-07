import { LucideIcon } from "lucide-react";

interface Props {
  label: string;
  value: string;
  icon: LucideIcon;
  tone?: "default" | "good" | "warn";
}

export function MetricTile({ label, value, icon: Icon, tone = "default" }: Props) {
  const toneCls = {
    default: "text-neutral-900",
    good: "text-emerald-600",
    warn: "text-rose-600",
  }[tone];

  return (
    <div className="card flex min-h-[120px] flex-col justify-between p-4 sm:p-5">
      <div className="flex items-center justify-between gap-3">
        <span className="text-[10px] uppercase tracking-widest text-neutral-500 sm:text-[11px]">{label}</span>
        <Icon size={14} className="shrink-0 text-neutral-500" />
      </div>
      <div className={`break-words text-xl font-semibold sm:text-2xl ${toneCls}`}>{value}</div>
    </div>
  );
}
