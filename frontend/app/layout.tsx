import "./globals.css";
import type { Metadata } from "next";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "CloudOps AI",
  description: "CloudOps AI dashboard for telemetry, RAG, and AI incident response.",
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
