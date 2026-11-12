import type { NextRequest } from "next/server";
import { cookies } from "next/headers";
import { isSameOrigin } from "@/lib/same-origin";

export const dynamic = "force-dynamic";

async function forward(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const target = path.join("/");
  const memberRoute = target === "members" || /^members\/[0-9a-f-]{36}$/i.test(target);
  if (!memberRoute && !["options", "google/start", "google/callback"].includes(target)) return new Response(null, { status: 404 });
  if (request.method !== "GET" && (!memberRoute || !isSameOrigin(request))) return new Response(null, { status: 403 });
  const headers = new Headers();
  if (memberRoute) {
    const token = request.headers.get("x-api-key") || (await cookies()).get("autobench-session")?.value;
    if (token) headers.set("x-api-key", token);
    headers.set("content-type", "application/json");
  }
  if (target === "google/callback") {
    const cookie = request.headers.get("cookie");
    if (cookie) headers.set("cookie", cookie);
  }
  try {
    const response = await fetch(`${process.env.AUTOBENCH_API_URL ?? "http://127.0.0.1:8000"}/v1/auth/${target}${request.nextUrl.search}`, {
      method: request.method, headers, redirect: "manual", cache: "no-store", signal: AbortSignal.timeout(35_000),
      ...(request.method === "PATCH" ? { body: await request.arrayBuffer() } : {}),
    });
    const output = new Headers({ "cache-control": "no-store", "referrer-policy": "no-referrer" });
    for (const name of ["content-type", "location"]) {
      const value = response.headers.get(name);
      if (value) output.set(name, value);
    }
    for (const cookie of response.headers.getSetCookie()) output.append("set-cookie", cookie);
    return new Response(response.body, { status: response.status, headers: output });
  } catch {
    return Response.json({ detail: "Sign-in is temporarily unavailable. Please try again." }, { status: 502 });
  }
}
export { forward as GET, forward as PATCH };
