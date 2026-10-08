"use client";

import { CheckCircle2, Sparkles } from "lucide-react";
import Link from "next/link";
import useSWR from "swr";
import { useState } from "react";
import { LoadMore } from "@/components/LoadMore";
import { api, fetcher } from "@/lib/api";
import { usePaged } from "@/lib/paged";
import { needsRole, useSession } from "@/lib/session";

interface Alert {
  id: number;
  source: string;
  severity: string;
  title: string;
  detail: string;
  status: string;
  created_at: string;
}

interface Incident {
  id: number;
  title: string;
  summary: string;
  root_cause: string;
  recommendation: string;
  confidence: number;
  status: string;
}

interface Scenarios {
  scenarios: string[];
}

const severityClass: Record<string, string> = {
  critical: "bg-rose-100 text-rose-700",
  high: "bg-amber-100 text-amber-700",
  medium: "bg-yellow-100 text-yellow-700",
  low: "bg-emerald-100 text-emerald-700",
};

export default function AlertsPage() {
  const { can } = useSession();
  const mayOperate = can("operator");
  const {
    items: alerts,
    total,
    hasMore,
    loadingMore,
    loadMore,
    mutate,
  } = usePaged<Alert>("/alerts");
  const { data: scenarios } = useSWR<Scenarios>("/alerts/scenarios", fetcher);
  const [triaging, setTriaging] = useState<number | null>(null);
  const [incident, setIncident] = useState<Incident | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function simulate(scenario: string) {
    if (!scenario) return;
    setError(null);
    await api("/alerts/simulate", { method: "POST", body: JSON.stringify({ scenario }) });
    await mutate();
  }

  async function acknowledge(id: number) {
    setError(null);
    await api(`/alerts/${id}/ack`, { method: "POST" });
    await mutate();
  }

  async function triage(id: number) {
    setTriaging(id);
    setIncident(null);
    setError(null);
    try {
      const nextIncident = await api<Incident>(`/incidents/triage/${id}`, { method: "POST" });
      setIncident(nextIncident);
      await mutate();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setTriaging(null);
    }
  }

  return (
    <div className="pt-2 sm:pt-4">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Alerts</h1>
          <p className="mt-1 text-sm text-neutral-500">Inbound telemetry alerts awaiting AI triage.</p>
        </div>
        <select
          className="input sm:max-w-[260px]"
          defaultValue=""
          onChange={(event) => simulate(event.target.value)}
          aria-label="Simulate alert"
          disabled={!mayOperate}
          title={mayOperate ? undefined : needsRole("operator")}
        >
          <option value="">Simulate alert…</option>
          {(scenarios?.scenarios ?? []).map((scenario) => (
            <option key={scenario} value={scenario}>{scenario}</option>
          ))}
        </select>
      </div>

      {error && <div className="card mb-4 p-4 text-sm text-rose-700">{error}</div>}

      <div className="card divide-y divide-black/5 overflow-hidden">
        {alerts.length === 0 && <div className="p-8 text-center text-sm text-neutral-500">No alerts. Simulate one to seed the queue.</div>}
        {alerts.map((alert) => (
          <div key={alert.id} className="grid gap-4 p-4 lg:grid-cols-[1fr_auto] lg:items-center">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded-full px-2 py-0.5 text-[10px] uppercase tracking-widest ${severityClass[alert.severity] ?? "bg-neutral-100 text-neutral-700"}`}>
                  {alert.severity}
                </span>
                <span className="font-medium">{alert.title}</span>
                <span className="text-xs text-neutral-500">{alert.source} · {alert.status}</span>
              </div>
              <div className="mt-1 text-sm text-neutral-600">{alert.detail}</div>
            </div>
            <div className="flex flex-wrap gap-2 lg:justify-end">
              <button
                onClick={() => triage(alert.id)}
                disabled={triaging === alert.id || !mayOperate}
                title={mayOperate ? undefined : needsRole("operator")}
                className="btn-primary gap-2 bg-brand-rose hover:bg-[#9c3141]"
              >
                <Sparkles size={14} />
                {triaging === alert.id ? "Analyzing…" : "AI Triage"}
              </button>
              <button
                onClick={() => acknowledge(alert.id)}
                disabled={!mayOperate}
                title={mayOperate ? undefined : needsRole("operator")}
                className="btn-secondary gap-2"
              >
                <CheckCircle2 size={14} />
                Ack
              </button>
            </div>
          </div>
        ))}
        <LoadMore
          className="border-t border-black/5 px-4 py-3"
          showing={alerts.length}
          total={total}
          hasMore={hasMore}
          loading={loadingMore}
          onLoadMore={loadMore}
        />
      </div>

      {incident && (
        <div className="card mt-5 p-4 sm:p-5">
          <div className="mb-3 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
            <h2 className="font-semibold">AI Recommendation · {incident.title}</h2>
            <div className="flex items-center gap-3">
              <span className="text-xs text-neutral-500">Confidence {Math.round(incident.confidence * 100)}%</span>
              <Link
                href={`/incidents/${incident.id}`}
                className="shrink-0 text-xs text-brand-rose underline-offset-2 hover:underline"
              >
                Open timeline
              </Link>
            </div>
          </div>
          <div className="mb-3 text-sm text-neutral-600">{incident.summary}</div>
          <div className="mb-3 rounded-xl bg-white p-3 text-sm text-neutral-700">Root cause: {incident.root_cause}</div>
          <pre className="whitespace-pre-wrap rounded-xl bg-neutral-950 p-4 font-sans text-sm text-white">{incident.recommendation}</pre>
        </div>
      )}
    </div>
  );
}
