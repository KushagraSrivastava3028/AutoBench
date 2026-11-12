"use client";
import { useQuery } from "@tanstack/react-query";
import { request, ApiError } from "./api";
import type { Session } from "./types";

export function useAccess(scope: "run" | "author" | "admin") {
  const query = useQuery({ queryKey: ["session"], queryFn: () => request<Session>("/console/session") });
  const allowed = query.data?.scopes.includes(scope) ?? false;
  const reason = query.isPending ? "Checking permissions…" : query.error instanceof ApiError && query.error.status === 401 ? "Sign in to continue." : query.isError ? "The service could not be reached." : !allowed ? `This action requires ${scope} permission.` : undefined;
  return { allowed, reason, loading: query.isPending };
}
