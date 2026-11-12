"use client";
import { useEffect } from "react";
export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => { console.error("AutoBench page error", error.digest ?? error.name); }, [error]);
  return <div className="panel p-10 text-center"><h1 className="text-2xl font-semibold">This page couldn’t load.</h1><p className="my-4 text-sm text-ink-muted">Check your connection and try again. Your saved evaluations are unchanged.</p><button className="btn btn-primary" onClick={reset}>Try again</button></div>;
}
