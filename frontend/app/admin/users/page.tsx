"use client";

import { ShieldCheck, UserPlus } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { api, fetcher } from "@/lib/api";
import { needsRole, useSession, type Role } from "@/lib/session";

interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
}

interface UserPage {
  items: User[];
  total: number;
}

const ROLES: Role[] = ["viewer", "operator", "admin"];

const ROLE_HINT: Record<Role, string> = {
  viewer: "Read-only access",
  operator: "Can run alerts, incidents, post-mortems and the sandbox",
  admin: "Everything, plus managing accounts",
};

export default function UsersPage() {
  const { can, isLoading } = useSession();
  const mayAdminister = can("admin");

  // A null key is SWR's "do not fetch": a non-admin never asks for a list the API would
  // refuse, so the page cannot leak the roster through a failed request either.
  const { data, error, mutate } = useSWR<UserPage>(mayAdminister ? "/users" : null, fetcher);

  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  async function createAccount() {
    if (!email.trim() || password.length < 8) {
      setActionError("An email and a password of at least 8 characters are required.");
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      await api("/users", {
        method: "POST",
        body: JSON.stringify({ email, name, password, role }),
      });
      setEmail("");
      setName("");
      setPassword("");
      setRole("viewer");
      await mutate();
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function changeRole(user: User, nextRole: Role) {
    setActionError(null);
    try {
      await api(`/users/${user.id}`, {
        method: "PATCH",
        body: JSON.stringify({ role: nextRole }),
      });
      await mutate();
    } catch (err) {
      setActionError((err as Error).message);
    }
  }

  if (isLoading) {
    return <p className="pt-4 text-sm text-neutral-500">Loading…</p>;
  }

  if (!mayAdminister) {
    return (
      <div className="card mt-4 max-w-xl p-5">
        <div className="mb-2 inline-flex items-center gap-2 text-sm font-semibold">
          <ShieldCheck size={16} /> Account management
        </div>
        <p className="text-sm text-neutral-600">{needsRole("admin")}</p>
      </div>
    );
  }

  return (
    <div className="grid gap-5 pt-2 sm:pt-4 lg:grid-cols-[1fr_380px] lg:gap-6">
      <div>
        <div className="mb-4">
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Users</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Who can sign in, and what each account is allowed to change.
          </p>
        </div>

        {(actionError || error) && (
          <div className="card mb-4 p-4 text-sm text-rose-700">
            {actionError ?? (error as Error).message}
          </div>
        )}

        <div className="card divide-y divide-black/5 overflow-hidden">
          {(data?.items ?? []).map((user) => (
            <div
              key={user.id}
              className="grid gap-3 p-4 sm:grid-cols-[1fr_auto] sm:items-center"
            >
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">{user.name}</div>
                <div className="truncate text-xs text-neutral-500">{user.email}</div>
              </div>
              <select
                value={user.role}
                onChange={(event) => changeRole(user, event.target.value as Role)}
                className="input w-full sm:max-w-[170px]"
                aria-label={`Role for ${user.email}`}
                title={ROLE_HINT[user.role]}
              >
                {ROLES.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </div>
          ))}
          {data?.items.length === 0 && (
            <div className="p-8 text-center text-sm text-neutral-500">No accounts yet.</div>
          )}
        </div>
      </div>

      <div>
        <div className="mb-4">
          <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Add account</h2>
          <p className="mt-1 text-sm text-neutral-500">
            New accounts start as viewers unless told otherwise.
          </p>
        </div>
        <div className="card p-4 sm:p-5">
          <input
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="name@company.com"
            aria-label="Email"
            className="input mb-2"
          />
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Display name"
            aria-label="Display name"
            className="input mb-2"
          />
          <input
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Temporary password (min 8 characters)"
            aria-label="Temporary password"
            type="password"
            className="input mb-2"
          />
          <select
            value={role}
            onChange={(event) => setRole(event.target.value as Role)}
            aria-label="Role"
            className="input mb-3"
          >
            {ROLES.map((option) => (
              <option key={option} value={option}>
                {option} — {ROLE_HINT[option]}
              </option>
            ))}
          </select>
          <button onClick={createAccount} disabled={busy} className="btn-primary gap-2">
            <UserPlus size={15} />
            {busy ? "Creating…" : "Create account"}
          </button>
        </div>
      </div>
    </div>
  );
}
