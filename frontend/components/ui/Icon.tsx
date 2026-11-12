const paths: Record<string, string> = {
  overview: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  runs: "M9 5l11 7-11 7z M4 5v14", compare: "M3 7h16 M15 3l4 4-4 4 M21 17H5 M9 13l-4 4 4 4",
  leaderboard: "M5 20V12 M12 20V4 M19 20V8", suites: "M3 5h18v16H3z M3 10h18 M9 10v11",
  tasks: "M8 3h8l4 4v14H4V3z M8 12l3 3 5-6", agents: "M8 4h8v3H8z M5 8h14v11H5z M9 12h.01 M15 12h.01 M9 16h6 M2 11v5 M22 11v5",
  settings: "M3 6h18 M3 12h18 M3 18h18 M8 3v6 M16 9v6 M10 15v6", ops: "M2 12h5l3-8 4 16 3-8h5",
  search: "M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M15 15l6 6", plus: "M12 5v14 M5 12h14",
  arrow: "M4 12h16 M14 6l6 6-6 6", chevron: "M9 5l7 7-7 7", down: "M6 9l6 6 6-6",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1 1 M18 18l1 1 M5 19l1-1 M18 6l1-1",
  moon: "M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11", menu: "M4 6h16 M4 12h16 M4 18h16", close: "M6 6l12 12 M18 6L6 18",
  coins: "M12 3c-5 0-8 2-8 4s3 4 8 4 8-2 8-4-3-4-8-4 M4 7v5c0 2 3 4 8 4s8-2 8-4V7 M4 12v5c0 2 3 4 8 4s8-2 8-4v-5",
  check: "M5 12l4 4L19 6", alert: "M12 3L2 21h20z M12 9v5 M12 17h.01", clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l3 2",
  refresh: "M20 7a9 9 0 1 0 1 8 M20 3v5h-5",
};
export function Icon({ name, size = 18, className }: { name: string; size?: number; className?: string }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}><path d={paths[name] ?? paths.overview} /></svg>;
}
