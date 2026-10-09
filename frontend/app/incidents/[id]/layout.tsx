import type { Metadata } from "next";

/**
 * The incident's own title would need the incident, which the page fetches in the browser, so the
 * tab carries the section rather than a stale or empty number.
 */
export const metadata: Metadata = { title: "Incident" };

export default function IncidentLayout({ children }: { children: React.ReactNode }) {
  return children;
}
