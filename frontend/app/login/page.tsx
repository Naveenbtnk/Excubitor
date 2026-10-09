"use client";

import { ArrowRight, Eye, EyeOff, LoaderCircle, TriangleAlert } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useState } from "react";
import { BrandPanel } from "@/components/BrandPanel";
import { Logo } from "@/components/Logo";
import { api } from "@/lib/api";

/**
 * The sign-in form. Nothing about the exchange changed when the page was rebuilt -- same POST to
 * `/auth/login`, same httpOnly cookie the API sets, same `next` destination and the same
 * sentence out of a 401 or a 422 -- only the markup around it.
 */
function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  // Empty on purpose. Pre-filling the seeded admin's credentials turns a sign-in page into a
  // button that anyone who can reach the page may press.
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
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
    <form onSubmit={submit} className="card p-6 sm:p-8">
      <div className="mb-7">
        <h2 className="text-2xl font-semibold tracking-tight">Sign in</h2>
        <p className="mt-2 text-sm leading-6 text-neutral-500">
          Excubitor has been watching the stack. Sign in to see what it found.
        </p>
      </div>

      <div className="space-y-5">
        <div>
          <label htmlFor="email" className="mb-1.5 block text-sm font-medium">
            Email
          </label>
          <input
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="input"
            placeholder="you@example.com"
          />
        </div>

        <div>
          <label htmlFor="password" className="mb-1.5 block text-sm font-medium">
            Password
          </label>
          {/* The toggle is inside the field's own box so the label still points at the input and
              the value survives the type switch. */}
          <div className="relative">
            <input
              id="password"
              name="password"
              type={reveal ? "text" : "password"}
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="input pr-11"
              placeholder="••••••••"
            />
            <button
              type="button"
              onClick={() => setReveal((value) => !value)}
              aria-pressed={reveal}
              aria-label={reveal ? "Hide password" : "Show password"}
              className="absolute inset-y-0 right-0 grid w-11 place-items-center rounded-r-xl text-neutral-500 transition hover:text-neutral-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-rose/40"
            >
              {reveal ? <EyeOff size={16} aria-hidden /> : <Eye size={16} aria-hidden />}
            </button>
          </div>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="mt-6 flex items-start gap-2.5 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-3 text-sm text-rose-800"
        >
          <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0 text-brand-rose" />
          <span>{error}</span>
        </div>
      )}

      {/* A submit button, not a click handler, so Enter in either field signs in. */}
      <button type="submit" disabled={loading} aria-busy={loading} className="btn-primary mt-7 w-full py-2.5">
        {loading ? (
          <>
            <LoaderCircle size={16} aria-hidden className="mr-2 animate-spin" />
            Signing in…
          </>
        ) : (
          <>
            Sign in
            <ArrowRight size={16} aria-hidden className="ml-2" />
          </>
        )}
      </button>
    </form>
  );
}

export default function LoginPage() {
  return (
    /* The root layout centres the console in a padded container; the sign-in page cancels those
       paddings with the same negative margins the nav uses, so the brand panel runs to the edges. */
    <main className="-mx-3 -mb-10 min-h-screen sm:-mx-5 sm:-mb-16 lg:-mx-8 lg:grid lg:grid-cols-[1.05fr_1fr] xl:grid-cols-[1.15fr_1fr]">
      <BrandPanel />

      <div className="flex min-h-screen flex-col justify-center px-4 py-12 sm:px-8 lg:px-12">
        {/* Below `lg` the panel above is hidden, so the brand collapses to a header. */}
        <div className="mb-8 flex items-center justify-center gap-3 lg:hidden">
          <div className="grid size-11 shrink-0 place-items-center rounded-2xl bg-neutral-900 text-brand-cream">
            <Logo size={22} />
          </div>
          <div className="leading-tight">
            <p className="text-lg font-semibold tracking-tight">Excubitor</p>
            <p className="text-[11px] uppercase tracking-[0.22em] text-neutral-500">CloudOps AI</p>
          </div>
        </div>

        <div className="w-full max-w-md self-center">
          <Suspense
            fallback={<div className="card p-8 text-sm text-neutral-500">Loading sign in…</div>}
          >
            <LoginForm />
          </Suspense>

          <p className="mt-5 text-center text-xs leading-5 text-neutral-500">
            The seeded administrator signs in with the <code className="text-neutral-600">ADMIN_EMAIL</code>{" "}
            and <code className="text-neutral-600">ADMIN_PASSWORD</code> configured for the backend.
          </p>
        </div>
      </div>
    </main>
  );
}
