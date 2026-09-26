"use client";

import { Pill } from "@/components/ui";
import type { InvoiceRow } from "@/lib/types";

export function StatusPill({ row }: { row: Pick<InvoiceRow, "status" | "days_overdue" | "days_to_due"> }) {
  if (row.status === "paid") return <Pill tone="good">Paid</Pill>;
  if (row.status === "disputed") return <Pill tone="serious">Disputed</Pill>;
  if (row.status === "overdue") return <Pill tone={row.days_overdue >= 60 ? "critical" : "warning"}>{row.days_overdue}d overdue</Pill>;
  return <Pill tone="neutral">{row.days_to_due === 0 ? "Due today" : `Due in ${row.days_to_due}d`}</Pill>;
}

export function scoreTone(score: number): "good" | "warning" | "critical" {
  return score >= 80 ? "good" : score >= 50 ? "warning" : "critical";
}
