"use client";

import { Pill } from "@/components/ui";
import type { InvoiceRow } from "@/lib/types";

export function StatusPill({ row }: { row: Pick<InvoiceRow, "status" | "days_overdue"> }) {
  if (row.status === "paid") return <Pill tone="good">Paid</Pill>;
  if (row.status === "disputed") return <Pill tone="serious">Disputed</Pill>;
  if (row.status === "overdue") return <Pill tone={row.days_overdue >= 60 ? "critical" : "warning"}>{row.days_overdue}d overdue</Pill>;
  return <Pill tone="neutral">Due in {Math.max(0, -row.days_overdue)}d</Pill>;
}

export function scoreTone(score: number): "good" | "warning" | "critical" {
  return score >= 80 ? "good" : score >= 50 ? "warning" : "critical";
}
