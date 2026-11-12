import type { NextRequest } from "next/server";

/** Compare with the browser-facing host, since Next can normalize its internal URL. */
export function isSameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return true;
  try {
    const source = new URL(origin);
    if (!["http:", "https:"].includes(source.protocol)) return false;
    if (process.env.AUTOBENCH_PUBLIC_URL) {
      return source.origin === new URL(process.env.AUTOBENCH_PUBLIC_URL).origin;
    }
    const host = request.headers.get("host") ?? request.nextUrl.host;
    return source.host.toLowerCase() === host.toLowerCase() && source.protocol === request.nextUrl.protocol;
  } catch {
    return false;
  }
}
