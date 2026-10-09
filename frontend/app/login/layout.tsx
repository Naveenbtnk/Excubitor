import type { Metadata } from "next";

/** See `app/alerts/layout.tsx` for why route titles live in a layout here. */
export const metadata: Metadata = { title: "Sign in" };

export default function LoginLayout({ children }: { children: React.ReactNode }) {
  return children;
}
