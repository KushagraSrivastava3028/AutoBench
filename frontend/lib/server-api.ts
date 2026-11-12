import "server-only";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { createApi, safe as fallbackRead, ApiError } from "./api";
export { ApiError } from "./api";
export async function safe<T>(promise: Promise<T>, fallback: T): Promise<T> {
  try { return await fallbackRead(promise, fallback); }
  catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
    throw error;
  }
}
export const api = createApi(async () => {
  const token = (await cookies()).get("autobench-session")?.value;
  const headers: Record<string, string> = {};
  if (token) headers["x-api-key"] = token;
  return headers;
});
