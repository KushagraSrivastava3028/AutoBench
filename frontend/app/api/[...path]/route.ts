import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { isSameOrigin } from "@/lib/same-origin";
export const dynamic = "force-dynamic";

async function forward(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (path.some(p => !p || p === "." || p === ".." || /[\\/]/.test(p))) return Response.json({ title: "Invalid path" }, { status: 400 });
  if (!["GET", "HEAD"].includes(request.method)) {
    if (!isSameOrigin(request)) return Response.json({ title: "Cross-origin mutation refused", detail: "Open AutoBench on its configured address before making changes." }, { status: 403 });
  }
  const headers = new Headers();
  for (const name of ["content-type", "accept", "last-event-id", "idempotency-key"]) { const value = request.headers.get(name); if (value) headers.set(name, value); }
  const token = request.headers.get("x-api-key") || (await cookies()).get("autobench-session")?.value;
  if (token) headers.set("x-api-key", token);
  const base = process.env.AUTOBENCH_API_URL ?? "http://127.0.0.1:8000";
  try {
    const response = await fetch(`${base}/v1/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`, {
      method: request.method, headers, cache: "no-store", redirect: "manual", signal: request.signal,
      ...(!["GET", "HEAD"].includes(request.method) ? { body: await request.arrayBuffer() } : {}),
    });
    const output = new Headers({ "cache-control": "no-store", "x-content-type-options": "nosniff" });
    for (const name of ["content-type", "content-disposition", "last-event-id"]) { const value = response.headers.get(name); if (value) output.set(name, value); }
    if (response.headers.get("content-type")?.includes("text/event-stream")) output.set("X-Accel-Buffering", "no");
    return new Response(response.body, { status: response.status, headers: output });
  } catch {
    return Response.json({ title: "API unavailable", detail: "The evaluation service could not be reached. Please try again." }, { status: 502 });
  }
}
export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE, forward as HEAD };
