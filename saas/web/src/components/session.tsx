"use client";

import { createContext, useContext } from "react";
import type { Session } from "@/lib/types";

export const SessionContext = createContext<{ session: Session; refresh: () => Promise<void> } | null>(null);

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession outside the app shell");
  return ctx;
}

/** What the signed-in person may do in the active business. The API enforces
 *  every one of these; the UI only hides what would be refused anyway. */
export function useRole() {
  const role = useSession().session.active_business?.role ?? "viewer";
  return {
    role,
    canWrite: role !== "viewer",
    canManage: role === "owner" || role === "admin",
    isOwner: role === "owner",
  };
}
