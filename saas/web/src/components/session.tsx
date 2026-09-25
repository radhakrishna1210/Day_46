"use client";

import { createContext, useContext } from "react";
import type { Session } from "@/lib/types";

export const SessionContext = createContext<{ session: Session; refresh: () => Promise<void> } | null>(null);

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession outside the app shell");
  return ctx;
}
