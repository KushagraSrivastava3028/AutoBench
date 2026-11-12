"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const router = useRouter();
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [google, setGoogle] = useState<boolean | null>(null);
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("error")) setError("Google sign-in could not be completed. Use an allowed Workspace account and try again, or contact your administrator.");
    void fetch("/api/auth/options", { cache: "no-store" }).then(async result => {
      if (!result.ok) throw new Error();
      setGoogle((await result.json()).google_enabled === true);
    }).catch(() => { setGoogle(false); setError("Sign-in options are temporarily unavailable. Please try again shortly."); });
  }, []);
  const keyForm = <form className="space-y-4" onSubmit={async e => {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const response = await fetch("/api/session", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ key }) });
      if (!response.ok) throw new Error((await response.json()).detail);
      setKey(""); router.push("/"); router.refresh();
    } catch (err) { setError(err instanceof Error ? err.message : "Sign-in failed."); }
    finally { setBusy(false); }
  }}>
    <label className="block text-sm font-medium">API key<input className="input mt-2 w-full" type="password" autoComplete="off" placeholder="ab_…" required value={key} onChange={e => setKey(e.target.value)} /></label>
    <button className="btn btn-primary w-full" disabled={busy}>{busy ? "Signing in…" : "Continue with API key"}</button>
  </form>;
  return <div className="mx-auto my-14 max-w-md rounded-2xl border border-border-subtle bg-surface p-8">
    <div className="eyebrow">Welcome to AutoBench</div>
    <h1 className="text-3xl font-semibold tracking-tight">Sign in to your workspace.</h1>
    <p className="my-4 text-sm text-ink-muted">Use your organization’s account to access evaluations.</p>
    {error && <p role="alert" className="my-4 text-sm text-fail-agent">{error}</p>}
    {google === null ? <p role="status" className="text-sm text-ink-muted">Loading sign-in options…</p> : google ? <>
      <a className="btn btn-primary w-full" href="/api/auth/google/start">Continue with Google Workspace</a>
      <p className="mt-3 text-xs text-ink-muted">New members receive viewer access. Your administrator manages workspace permissions.</p>
      <details className="mt-6"><summary className="mb-4 cursor-pointer text-sm text-ink-muted">Administrator or integration API key</summary>{keyForm}</details>
    </> : keyForm}
    <p className="mt-5 text-xs text-ink-muted">Sessions use an HttpOnly cookie. Sign out when using a shared device.</p>
  </div>;
}
