import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "@/components/nav";

export const metadata: Metadata = {
  title: "ResearchOS",
  description: "Autonomous thesis research orchestrator",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <aside className="sidebar">
            <div className="brand">
              ResearchOS
              <small>research control plane</small>
            </div>
            <Nav />
          </aside>
          <main className="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
