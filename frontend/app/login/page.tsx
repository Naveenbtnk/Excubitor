"use client";

import { Activity, ArrowRight, LockKeyhole } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useState } from "react";
import { api } from "@/lib/api";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("admin@cloudops.local");
  const [password, setPassword] = useState("ChangeMe123!");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      router.push(params.get("next") || "/");
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={submit} className="card p-5 sm:p-6">
      <div className="mb-5">
        <div className="mb-2 inline-flex items-center gap-2 rounded-full bg-rose-50 px-3 py-1 text-xs font-medium text-brand-rose">
          <LockKeyhole size={13} />
          Admin access
        </div>
        <h2 className="text-2xl font-semibold tracking-tight">Sign in to dashboard</h2>
        <p className="mt-1 text-sm text-neutral-500">Use the seeded admin from your backend environment variables.</p>
      </div>

      <label className="mb-3 block text-sm font-medium">
        Email
        <input className="input mt-1" value={email} onChange={(event) => setEmail(event.target.value)} type="email" autoComplete="email" />
      </label>
      <label className="mb-4 block text-sm font-medium">
        Password
        <input className="input mt-1" value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" />
      </label>

      {error && <div className="mb-4 rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>}

      <button className="btn-primary w-full gap-2" disabled={loading}>
        {loading ? "Signing in…" : "Sign in"}
        <ArrowRight size={16} />
      </button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="grid min-h-screen place-items-center px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center justify-center gap-3">
          <div className="grid size-11 place-items-center rounded-2xl bg-neutral-900 text-white">
            <Activity size={22} />
          </div>
          <div>
            <h1 className="text-xl font-semibold">CloudOps AI</h1>
            <p className="text-xs uppercase tracking-widest text-neutral-500">Secure ops console</p>
          </div>
        </div>
        <Suspense fallback={<div className="card p-6 text-sm text-neutral-500">Loading sign in…</div>}>
          <LoginForm />
        </Suspense>
      </div>
    </main>
  );
}
