import type { Metadata } from "next";

/**
 * A route title lives in a layout because the page itself is a client component, which cannot
 * export metadata. The root layout's template turns this into "Excubitor — Alerts", so the brand
 * stays first in every tab.
 */
export const metadata: Metadata = { title: "Alerts" };

export default function AlertsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
