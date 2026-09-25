"use client";

import { useCallback, useEffect, useState } from "react";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function messageFrom(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length) {
      const first = detail[0] as { msg?: string; loc?: unknown[] };
      const field = Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1]) : "";
      return `${field ? field.replace(/_/g, " ") + ": " : ""}${(first.msg ?? "Invalid value").replace(/^Value error, /, "")}`;
    }
  }
  return status >= 500 ? "Something went wrong on our side. Please try again." : "Request failed";
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const res = await fetch(`/api${path}`, {
    credentials: "include",
    ...rest,
    headers: json !== undefined ? { "Content-Type": "application/json", ...headers } : headers,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401 && typeof window !== "undefined" && window.location.pathname.startsWith("/app")) {
      const login = new URL(`/login?next=${encodeURIComponent(window.location.pathname)}`, window.location.origin);
      window.location.assign(login.toString());
    }
    throw new ApiError(res.status, messageFrom(body, res.status));
  }
  return body as T;
}

type Settled<T> = { key: string; data: T | null; error: string | null };

/** Fetch `path` (null = don't); `reload()` refetches. State is only ever set
 *  when a response arrives -- `loading` is derived, never set in an effect. */
export function useApi<T>(path: string | null) {
  const [tick, setTick] = useState(0);
  const key = path === null ? "" : `${path}#${tick}`;
  const [settled, setSettled] = useState<Settled<T>>({ key: "", data: null, error: null });

  useEffect(() => {
    if (path === null) return;
    let live = true;
    api<T>(path).then(
      (data) => { if (live) setSettled({ key, data, error: null }); },
      (e: unknown) => { if (live) setSettled((s) => ({ key, data: s.data, error: e instanceof Error ? e.message : "Request failed" })); },
    );
    return () => { live = false; };
  }, [path, key]);

  const reload = useCallback(async () => {
    setTick((t) => t + 1);
  }, []);
  const setData = useCallback((data: T) => setSettled((s) => ({ ...s, data })), []);

  return {
    data: settled.data,
    error: settled.key === key ? settled.error : null,
    loading: path !== null && settled.key !== key,
    reload,
    setData,
  };
}
