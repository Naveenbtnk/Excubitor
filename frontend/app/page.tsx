"use client";

import { ArrowUpRight, Cpu, Database, HeartPulse, Siren, Sparkles } from "lucide-react";
import Link from "next/link";
import useSWR from "swr";
import { MetricTile } from "@/components/MetricTile";
import { SystemHealthCard } from "@/components/SystemHealthCard";
import { api, fetcher } from "@/lib/api";
import { usePaged } from "@/lib/paged";
import { needsRole, useSession } from "@/lib/session";

interface Overview {
  status: string;
  active_alerts: number;
  log_entries: number;
  model: { provider: string; name: string };
  runbooks: { doc_id: string; title: string; source: string }[];
}

interface Alert {
  id: number;
  title: string;
  severity: string;
  detail: string;
  source: string;
  status: string;
}

interface Scenarios {
  scenarios: string[];
}

export default function OverviewPage() {
  const { can } = useSession();
  const mayOperate = can("operator");
  const { data, error, mutate } = useSWR<Overview>("/overview", fetcher);
  const { items: alerts, mutate: mutateAlerts } = usePaged<Alert>("/alerts", 5);
  const { data: scenarios } = useSWR<Scenarios>("/alerts/scenarios", fetcher);

  async function simulate(scenario: string) {
    // The control is disabled for anyone below Operator; returning here means a forced event
    // cannot send a request the UI has decided not to offer. The API refuses it regardless.
    if (!mayOperate || !scenario) return;
    await api("/alerts/simulate", {
      method: "POST",
      body: JSON.stringify({ scenario: scenario || null }),
    });
    await Promise.all([mutate(), mutateAlerts()]);
  }

  const activeAlerts = data?.active_alerts ?? 0;
  const healthy = data?.status === "Healthy";

  return (
    <div className="pt-2 sm:pt-4">
      <section className="grid gap-4 lg:grid-cols-[1.5fr_1fr] lg:gap-6">
        <div className="flex flex-col justify-between gap-5 rounded-2xl border border-black/5 bg-white/50 p-5 sm:p-6 lg:p-7">
          <div className="inline-flex w-fit items-center gap-2 rounded-full border border-black/5 bg-white/70 px-3 py-1.5 text-xs">
            <Sparkles size={12} className="text-brand-rose" />
            Autonomous incident response
          </div>

          <div>
            <h1 className="max-w-2xl text-3xl font-semibold leading-tight tracking-tight sm:text-4xl lg:text-5xl">
              Smarter ops with <span className="text-brand-rose">Excubitor.</span>
            </h1>
            <p className="mt-3 max-w-xl text-sm leading-6 text-neutral-600 sm:text-base">
              A production-style AIOps dashboard for telemetry, vector runbooks, Gemini triage, and incident post-mortems.
            </p>
          </div>

          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <Link href="/sandbox" className="btn-primary gap-2">
              Explore Sandbox <ArrowUpRight size={15} />
            </Link>
            <select
              className="input sm:max-w-[260px]"
              defaultValue=""
              onChange={(event) => simulate(event.target.value)}
              aria-label="Simulate incident"
              disabled={!mayOperate}
              title={mayOperate ? undefined : needsRole("operator")}
            >
              <option value="">Simulate incident…</option>
              {(scenarios?.scenarios ?? []).map((scenario) => (
                <option key={scenario} value={scenario}>
                  {scenario}
                </option>
              ))}
            </select>
          </div>
        </div>

        <SystemHealthCard
          activeAlerts={activeAlerts}
          model={data ? `${data.model.provider} (${data.model.name})` : "Loading…"}
          status={healthy ? "ALL SYSTEMS OPTIMAL" : "DEGRADED"}
        />
      </section>

      {error && <div className="card mt-5 p-4 text-sm text-rose-700">Backend not reachable: {(error as Error).message}</div>}

      <section className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4 lg:gap-4">
        <MetricTile label="Status" value={data?.status ?? "Loading"} icon={HeartPulse} tone={healthy ? "good" : "warn"} />
        <MetricTile label="Active Alerts" value={`${activeAlerts} Pending`} icon={Siren} tone={activeAlerts > 0 ? "warn" : "good"} />
        <MetricTile label="Log Entries" value={`${data?.log_entries ?? 0} Logged`} icon={Database} />
        <MetricTile label="AI Model" value={data?.model.name ?? "Loading"} icon={Cpu} />
      </section>

      <section className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-3 lg:gap-6">
        <div className="card lg:col-span-2">
          <div className="flex items-start justify-between gap-3 border-b border-black/5 p-4 sm:p-5">
            <div>
              <h3 className="font-semibold">Active Incidents & AI Recommendations</h3>
              <p className="text-xs text-neutral-500">Infrastructure alerts queued for analysis.</p>
            </div>
            <Link href="/alerts" className="shrink-0 text-sm text-brand-rose">
              View all
            </Link>
          </div>
          <div className="divide-y divide-black/5">
            {alerts.length === 0 && <div className="p-8 text-center text-sm text-neutral-500">No alerts yet — simulate one to start.</div>}
            {alerts.map((alert) => (
              <div key={alert.id} className="grid gap-2 p-4 sm:grid-cols-[1fr_auto] sm:items-center">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{alert.title}</div>
                  <div className="text-xs text-neutral-500">{alert.source} · {alert.severity} · {alert.status}</div>
                </div>
                <div className="text-sm text-neutral-600 sm:max-w-xs sm:truncate">{alert.detail}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="card p-4 sm:p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <h3 className="font-semibold">Indexed Runbooks</h3>
            <span className="text-xs text-brand-rose">Vector RAG</span>
          </div>
          <div className="flex flex-col gap-2">
            {(data?.runbooks ?? []).length === 0 && <div className="py-6 text-center text-sm text-neutral-500">No runbooks indexed. Add one in Knowledge.</div>}
            {data?.runbooks.map((runbook) => (
              <div key={runbook.doc_id} className="flex items-center justify-between gap-3 border-b border-black/5 py-2 text-sm last:border-none">
                <span className="truncate font-mono text-xs">{runbook.title}</span>
                <span className="shrink-0 text-xs text-emerald-600">Ready</span>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
