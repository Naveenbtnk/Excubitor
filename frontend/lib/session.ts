"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";

/**
 * The signed-in user's role, as the API reports it.
 *
 * Everything here is cosmetic. The server enforces the same rule on every request, and these
 * helpers exist so the UI does not offer a button that can only produce a 403.
 */
export type Role = "admin" | "operator" | "viewer";

export interface SessionUser {
  id: number;
  email: string;
  name: string;
  role: Role;
}

const ROLE_RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };

/**
 * Whether a role may perform something that needs ``minimum``.
 *
 * A missing or unrecognised role holds nothing -- the same fail-closed reading the API makes,
 * so the client can never believe it has more access than the server will grant.
 */
export function can(role: string | null | undefined, minimum: Role): boolean {
  if (!role) return false;
  const rank = ROLE_RANK[role as Role];
  return rank !== undefined && rank >= ROLE_RANK[minimum];
}

/** The reason to show next to a disabled control, phrased like the API's own 403. */
export function needsRole(minimum: Role): string {
  return `Requires the ${minimum} role or higher.`;
}

export function useSession() {
  const { data, error, isLoading } = useSWR<SessionUser>("/auth/me", fetcher);
  const role = data?.role;

  return {
    user: data,
    role,
    isLoading,
    error,
    can: (minimum: Role) => can(role, minimum),
  };
}
