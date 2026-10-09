import "./globals.css";
import type { Metadata } from "next";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  // The brand is the first thing in every title. Each route sets a plain section name in its own
  // layout and the template prefixes it, so the tab reads "Excubitor — Alerts" wherever you are.
  title: {
    default: "Excubitor — CloudOps AI",
    template: "Excubitor — %s",
  },
  // The favicon is app/icon.svg, which Next turns into a hashed <link rel="icon"> on its own.
  // The apple icon has no such convention for SVG (a file named apple-icon.svg is simply not
  // routed), so the same drawing is served from public/ and named here. Note that iOS itself
  // still wants a PNG for this one -- see the known limitations in the README.
  icons: { apple: "/apple-touch-icon.svg" },
  description:
    "Excubitor is a CloudOps AI console for alert triage, incident timelines, and RAG-powered post-mortems.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen" suppressHydrationWarning>
        <div className="mx-auto w-full max-w-7xl px-3 sm:px-5 lg:px-8">
          <Nav />
          <main className="pb-10 sm:pb-16">{children}</main>
        </div>
      </body>
    </html>
  );
}
