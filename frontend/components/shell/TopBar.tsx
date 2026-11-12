"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Icon } from "@/components/ui/Icon";
import { LaunchDialog } from "@/components/run/LaunchDialog";
import { CommandPalette } from "./CommandPalette";

export function TopBar() {
  const pathname = usePathname();
  const [launching, setLaunching] = useState(false);
  const [searching, setSearching] = useState(false);
  const [theme, setTheme] = useState("light");
  const health = useQuery({ queryKey: ["overview", 7, ""], queryFn: () => api.overview(7), refetchInterval: 15_000 });
  useEffect(() => {
    setTheme(document.documentElement.dataset.theme ?? "light");
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); setSearching(v => !v); }
      if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === "n") { event.preventDefault(); setLaunching(true); }
    };
    const launch = () => setLaunching(true);
    window.addEventListener("keydown", key); window.addEventListener("autobench:launch", launch);
    return () => { window.removeEventListener("keydown", key); window.removeEventListener("autobench:launch", launch); };
  }, []);
  const title = pathname === "/" ? "Overview" : pathname.split("/").filter(Boolean).map(v => v.length > 22 ? `${v.slice(0, 8)}…` : v.replace(/-/g, " ")).join(" / ");
  const state = health.isError ? "Unavailable" : !health.data ? "Checking" : health.data.workers_active === 0 ? "No workers" : health.data.alerts.some(a => a.tone === "infra") ? "Needs attention" : "Healthy";
  function toggleTheme() { const next = theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = next; setTheme(next); try { localStorage.setItem("autobench-theme", next); } catch {} }
  return <>
    <header className="app-topbar"><div className="topbar-breadcrumb"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => window.dispatchEvent(new Event("autobench:menu"))}><Icon name="menu" /></button><span className="breadcrumb-workspace">Workspace <span>/</span></span><strong>{title}</strong></div>
      <div className="topbar-actions"><button className="search-button" onClick={() => setSearching(true)}><Icon name="search" size={16} /><span>Search anything…</span><kbd>Ctrl K</kbd></button><Link href="/ops" className={`health-pill ${state === "Healthy" ? "healthy" : "attention"}`}><span className="status-dot" />Harness <span className="health-label">{state}</span></Link><button className="icon-button" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} onClick={toggleTheme}><Icon name={theme === "dark" ? "sun" : "moon"} /></button></div>
    </header>
    {launching && <LaunchDialog onClose={() => setLaunching(false)} />}{searching && <CommandPalette onClose={() => setSearching(false)} />}
  </>;
}
