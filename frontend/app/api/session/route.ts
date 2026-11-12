import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { isSameOrigin } from "@/lib/same-origin";
export async function POST(request: NextRequest) {
  if (!isSameOrigin(request)) return Response.json({ detail: "Cross-origin sign-in refused." }, { status: 403 });
  const body = await request.json().catch(() => null);
  if (!body || typeof body.key !== "string" || !body.key.startsWith("ab_") || body.key.length > 256) return Response.json({ detail: "Enter a valid AutoBench API key." }, { status: 400 });
  try {
    const result = await fetch(`${process.env.AUTOBENCH_API_URL ?? "http://127.0.0.1:8000"}/v1/console/session`, { headers: { "x-api-key": body.key }, cache: "no-store", signal: AbortSignal.timeout(10_000) });
    if (!result.ok) return Response.json({ detail: "The key is invalid, expired, or revoked." }, { status: 401 });
    (await cookies()).set("autobench-session", body.key, { httpOnly: true, sameSite: "strict", secure: process.env.AUTOBENCH_PUBLIC_URL?.startsWith("https://") || request.nextUrl.protocol === "https:", path: "/", maxAge: 8 * 60 * 60 });
    return Response.json({ ok: true }, { headers: { "cache-control": "no-store" } });
  } catch { return Response.json({ detail: "The evaluation service is unavailable. Try again shortly." }, { status: 502 }); }
}
export async function DELETE(request: NextRequest) {
  if (!isSameOrigin(request)) return new Response(null, { status: 403 });
  const token = (await cookies()).get("autobench-session")?.value;
  if (token?.startsWith("as_")) {
    try {
      const result = await fetch(`${process.env.AUTOBENCH_API_URL ?? "http://127.0.0.1:8000"}/v1/auth/logout`, {
        method: "POST", headers: { "x-api-key": token }, cache: "no-store", signal: AbortSignal.timeout(10_000),
      });
      if (!result.ok && result.status !== 401) return Response.json({ detail: "Sign-out could not be completed. Try again." }, { status: 502 });
    } catch { return Response.json({ detail: "Sign-out could not be completed. Try again." }, { status: 502 }); }
  }
  (await cookies()).delete("autobench-session");
  return new Response(null, { status: 204 });
}
