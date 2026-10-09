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
  // Both icons are named here rather than left to the filename conventions. `app/icon.svg` is
  // routed either way, but a metadata `icons` field replaces the file-convention link instead of
  // adding to it, so setting only the apple icon silently dropped the favicon. The apple icon has
  // no SVG convention at all, so the same drawing is served from public/. Note that iOS still
  // wants a PNG for that one -- see the known limitations in the README.
  icons: {
    icon: [{ url: "/icon.svg", type: "image/svg+xml" }],
    apple: "/apple-touch-icon.svg",
  },
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
