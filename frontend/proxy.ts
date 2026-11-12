import { NextResponse, type NextRequest } from "next/server";
export function proxy(request: NextRequest) {
  if (process.env.AUTOBENCH_AUTH_REQUIRED !== "true" || request.cookies.has("autobench-session")) return NextResponse.next();
  return NextResponse.redirect(new URL("/login", request.url));
}
export const config = { matcher: ["/((?!api|_next|login|healthz|favicon.ico).*)"] };
