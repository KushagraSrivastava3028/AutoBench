"use client";
import { useEffect, useRef, type ReactNode } from "react";
export function Modal({ children, onClose, label, sheet = false }: { children: ReactNode; onClose: () => void; label: string; sheet?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const closeRef = useRef(onClose); closeRef.current = onClose;
  useEffect(() => { const previous = document.activeElement as HTMLElement | null; const dialog = ref.current; dialog?.showModal(); return () => { dialog?.close(); previous?.focus(); }; }, []);
  return <dialog ref={ref} aria-label={label} className={`app-dialog ${sheet ? "trial-sheet" : ""}`} onCancel={e => { e.preventDefault(); closeRef.current(); }} onClick={e => { if (e.target === e.currentTarget) { const r = e.currentTarget.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) closeRef.current(); } }}>{children}</dialog>;
}
