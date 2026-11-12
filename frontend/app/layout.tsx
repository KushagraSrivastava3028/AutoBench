import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Sidebar } from "@/components/shell/Sidebar";
import { TopBar } from "@/components/shell/TopBar";
import { Providers } from "@/components/shell/Providers";

import "./globals.css";
import "./console.css";

export const metadata: Metadata = {
  title: {
    default: "Autobench",
    template: "%s · Autobench",
  },
  description:
    "A containerised, parallel evaluation platform for LLM agents. Grades the final container state, reports pass@k and pass^k with confidence intervals, and makes every trial replayable.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: `try{document.documentElement.dataset.theme=localStorage.getItem('autobench-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light')}catch{}` }} />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="bg-canvas text-ink antialiased">
        <a className="skip-link" href="#main-content">Skip to content</a>
        <Providers>
          <div className="app-shell">
            <Sidebar />
            <div className="app-frame">
              <TopBar />
              <main id="main-content" className="app-main" tabIndex={-1}>{children}</main>
            </div>
          </div>
        </Providers>
      </body>
    </html>
  );
}
