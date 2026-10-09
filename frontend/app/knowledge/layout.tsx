import type { Metadata } from "next";

/** See `app/alerts/layout.tsx` for why route titles live in a layout here. */
export const metadata: Metadata = { title: "Knowledge" };

export default function KnowledgeLayout({ children }: { children: React.ReactNode }) {
  return children;
}
