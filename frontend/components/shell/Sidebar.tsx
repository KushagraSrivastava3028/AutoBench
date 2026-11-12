"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Icon } from "@/components/ui/Icon";

const groups = [
  { label: "Workspace", items: [["/", "Overview", "overview"], ["/runs", "Runs", "runs"], ["/compare", "Compare", "compare"], ["/leaderboard", "Leaderboard", "leaderboard"]] },
  { label: "Library", items: [["/suites", "Suites", "suites"], ["/tasks", "Tasks", "tasks"], ["/agents", "Agents & models", "agents"]] },
  { label: "Manage", items: [["/ops", "Operations", "ops"], ["/settings", "Settings", "settings"]] },
];
export function Sidebar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  useEffect(() => { const show = () => setOpen(v => !v); window.addEventListener("autobench:menu", show); return () => window.removeEventListener("autobench:menu", show); }, []);
  useEffect(() => { setOpen(false); }, [pathname]);
  useEffect(() => { if (!open) return; const close = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); }; window.addEventListener("keydown", close); return () => window.removeEventListener("keydown", close); }, [open]);
  return <>
    {open && <button className="nav-backdrop" onClick={() => setOpen(false)} aria-label="Close navigation" />}
    <aside className={`app-sidebar ${open ? "is-open" : ""}`} aria-label="Main navigation">
      <Link href="/" className="brand"><span className="brand-mark"><Icon name="ops" size={22} /></span>AutoBench<span className="brand-period">.</span></Link>
      <div className="workspace-card"><span className="workspace-initial">A</span><div><strong>AutoBench workspace</strong><span>Evaluation console</span></div></div>
      <nav className="sidebar-navigation">{groups.map(group => <div className="nav-group" key={group.label}><p className="nav-label">{group.label}</p>{group.items.map(([href, label, icon]) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href!);
        return <Link href={href!} key={href} className="nav-item" aria-current={active ? "page" : undefined}><Icon name={icon!} size={18} /><span>{label}</span>{active && <span className="nav-active-dot" />}</Link>;
      })}</div>)}</nav>
      <div className="sidebar-bottom"><span className="workspace-initial"><Icon name="agents" /></span><div><strong>Built for reliable agents</strong><span>Measure. Understand. Improve.</span></div></div>
    </aside>
  </>;
}
