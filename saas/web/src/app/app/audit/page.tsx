"use client";

import { motion } from "motion/react";
import { Bot, Cpu, ScrollText, Search, User } from "lucide-react";
import { useEffect, useState } from "react";
import { Card, EmptyState, ErrorNote, PageHeader, Pill, Skeleton } from "@/components/ui";
import { useApi } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { AuditEntry } from "@/lib/types";

const SOURCE = {
  rule: { icon: Cpu, label: "Rule", tone: "brand" as const },
  llm: { icon: Bot, label: "AI", tone: "warning" as const },
  user: { icon: User, label: "Person", tone: "neutral" as const },
};

function humanAction(action: string) {
  return action.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export default function AuditPage() {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => { const t = setTimeout(() => setDebounced(q), 250); return () => clearTimeout(t); }, [q]);
  const { data, error, loading, reload } = useApi<{ entries: AuditEntry[]; has_more: boolean }>(`/audit?limit=200${debounced ? `&q=${encodeURIComponent(debounced)}` : ""}`);

  return (
    <>
      <PageHeader eyebrow="Append-only" title="Audit trail"
        description="Every money-related action, with who took it, why, and whether a rule, the AI or a person decided. Nothing here can be edited or deleted." />
      {error && <ErrorNote message={error} onRetry={reload} />}
      <label className="relative mb-5 block w-full sm:w-80">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-3" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search reason, invoice or buyer" aria-label="Search the audit trail"
          className="h-10 w-full rounded-xl border border-line-strong bg-surface pr-3 pl-9 text-sm outline-none focus:border-brand" />
      </label>

      <Card>
        {loading && !data ? (
          <div className="space-y-2 p-5">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-14" />)}</div>
        ) : !data?.entries.length ? (
          <EmptyState icon={<ScrollText className="size-6" />} title="Nothing recorded yet" body="Actions appear here the moment they happen." />
        ) : (
          <ol className="divide-y divide-line">
            {data.entries.map((e, i) => {
              const s = SOURCE[e.source] ?? SOURCE.user;
              return (
                <motion.li key={e.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(i, 15) * 0.02 }}
                  className="flex gap-4 px-5 py-4">
                  <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-surface-2 text-ink-2"><s.icon className="size-4" /></span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[14px] font-medium">{humanAction(e.action)}</span>
                      <Pill tone={s.tone} icon={false}>{s.label}</Pill>
                      {e.invoice_number && <span className="text-[12.5px] text-ink-3">{e.invoice_number}</span>}
                      {e.buyer_name && <span className="text-[12.5px] text-ink-3">· {e.buyer_name}</span>}
                    </div>
                    <p className="mt-1 text-[13px] leading-relaxed text-ink-2">{e.reason}</p>
                    <p className="mt-1 truncate text-[12px] text-ink-3 sm:hidden">{dateTime(e.at)} · {e.actor}</p>
                  </div>
                  <div className="hidden shrink-0 text-right text-[12px] text-ink-3 sm:block">
                    <div>{dateTime(e.at)}</div>
                    <div className="mt-0.5 max-w-[160px] truncate">{e.actor}</div>
                  </div>
                </motion.li>
              );
            })}
          </ol>
        )}
      </Card>
    </>
  );
}
