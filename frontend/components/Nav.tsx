"use client";

import { clsx } from "clsx";
import {
  BookOpen,
  FileText,
  LayoutDashboard,
  LogOut,
  Menu,
  Play,
  RotateCw,
  Siren,
  Users,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { Logo } from "@/components/Logo";
import { api } from "@/lib/api";
import { useSession, type Role } from "@/lib/session";

const tabs = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/sandbox", label: "Sandbox", icon: Play },
  { href: "/alerts", label: "Alerts", icon: Siren },
  { href: "/knowledge", label: "Knowledge", icon: BookOpen },
  { href: "/post-mortems", label: "Post-Mortems", icon: FileText },
  // User management is the one surface a non-admin has no use for, so the link is absent
  // rather than disabled. The page itself refuses anyone else regardless.
  { href: "/admin/users", label: "Users", icon: Users, role: "admin" as Role },
];

export function Nav() {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const isLogin = pathname === "/login";
  const { user, can } = useSession();
  const visibleTabs = tabs.filter(({ role }) => !role || can(role));
  const initials = (user?.name ?? "")
    .split(" ")
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();

  async function logout() {
    await api("/auth/logout", { method: "POST" });
    router.push("/login");
    router.refresh();
  }

  if (isLogin) {
    return null;
  }

  return (
    <header className="sticky top-0 z-40 -mx-3 mb-4 border-b border-black/5 bg-brand-cream/90 px-3 py-3 backdrop-blur sm:-mx-5 sm:px-5 lg:-mx-8 lg:px-8">
      <div className="flex items-center justify-between gap-3">
        <Link href="/" className="flex min-w-0 items-center gap-2">
          {/* The mark is cream on the same dark tile as the rest of the chrome, so the flame
              is a hole showing the tile through it rather than a second colour. */}
          <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-neutral-900 text-brand-cream">
            <Logo size={20} />
          </div>
          <div className="min-w-0 leading-tight">
            <div className="truncate text-sm font-semibold sm:text-base">Excubitor</div>
            <div className="hidden text-[10px] tracking-widest text-neutral-500 sm:block">
              CloudOps AI
            </div>
          </div>
        </Link>

        <nav className="pill-nav hidden lg:flex">
          {visibleTabs.map(({ href, label, icon: Icon }) => {
            const active = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                className={clsx("pill inline-flex items-center gap-1.5", active && "pill-active")}
              >
                <Icon size={14} />
                {label}
              </Link>
            );
          })}
        </nav>

        <div className="hidden items-center gap-2 lg:flex">
          <span className="rounded-full border border-black/5 bg-white/70 px-3 py-1.5 text-xs font-medium capitalize text-neutral-600">
            {user?.role ?? "…"}
          </span>
          <button
            onClick={() => router.refresh()}
            className="grid size-9 place-items-center rounded-full border border-black/5 bg-white/70 text-neutral-600"
            aria-label="Refresh dashboard"
          >
            <RotateCw size={14} />
          </button>
          <div className="flex items-center gap-2 rounded-full border border-black/5 bg-white/70 py-1 pl-1 pr-2">
            <div className="grid size-7 place-items-center rounded-full bg-rose-100 text-xs font-semibold text-brand-rose">
              {initials || "—"}
            </div>
            <span className="text-sm">{user?.name ?? "Loading…"}</span>
          </div>
          <button onClick={logout} className="grid size-9 place-items-center rounded-full border border-black/5 bg-white/70 text-neutral-600" aria-label="Logout">
            <LogOut size={14} />
          </button>
        </div>

        <button
          className="grid size-10 place-items-center rounded-full border border-black/5 bg-white/75 lg:hidden"
          onClick={() => setOpen((value) => !value)}
          aria-label="Toggle menu"
        >
          {open ? <X size={18} /> : <Menu size={18} />}
        </button>
      </div>

      {open && (
        <div className="mt-3 rounded-2xl border border-black/5 bg-white/90 p-2 shadow-sm lg:hidden">
          <nav className="grid gap-1">
            {visibleTabs.map(({ href, label, icon: Icon }) => {
              const active = pathname === href;
              return (
                <Link
                  key={href}
                  href={href}
                  onClick={() => setOpen(false)}
                  className={clsx(
                    "flex items-center gap-2 rounded-xl px-3 py-2 text-sm text-neutral-700",
                    active && "bg-neutral-900 text-white"
                  )}
                >
                  <Icon size={16} />
                  {label}
                </Link>
              );
            })}
          </nav>
          <button onClick={logout} className="mt-2 flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-neutral-700">
            <LogOut size={16} />
            Logout
          </button>
        </div>
      )}
    </header>
  );
}
