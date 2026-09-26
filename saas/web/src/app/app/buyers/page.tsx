"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { motion } from "motion/react";
import { BellOff, Plus, Search, Users } from "lucide-react";
import { Suspense, useEffect, useMemo, useState } from "react";
import { BuyerForm } from "@/components/buyer-form";
import { ScoreRing } from "@/components/score-ring";
import { useRole } from "@/components/session";
import { Button, Card, EmptyState, ErrorNote, PageHeader, Pill, Skeleton, stagger } from "@/components/ui";
import { useApi } from "@/lib/api";
import { plural, rupees } from "@/lib/format";
import type { BuyerRow } from "@/lib/types";

function BuyersInner() {
  const { data, error, loading, reload } = useApi<BuyerRow[]>("/buyers");
  const params = useSearchParams();
  const router = useRouter();
  // ?new=1 (from the overview's "Add a buyer") opens the form straight away.
  const { canWrite } = useRole();
  const [creating, setCreating] = useState(() => canWrite && params.get("new") !== null);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"overdue" | "score" | "name">("overdue");

  useEffect(() => { if (params.get("new") !== null) router.replace("/app/buyers"); }, [params, router]);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = (data ?? []).filter((b) => !needle || b.name.toLowerCase().includes(needle) || (b.city ?? "").toLowerCase().includes(needle));
    return [...list].sort((a, b) =>
      sort === "name" ? a.name.localeCompare(b.name)
        : sort === "score" ? (a.score?.value ?? 0) - (b.score?.value ?? 0)
          : (b.overdue_paise ?? 0) - (a.overdue_paise ?? 0));
  }, [data, q, sort]);

  return (
    <>
      <PageHeader title="Buyers" description="Each buyer is scored from how they have actually paid you — the arithmetic is always one click away."
        actions={canWrite && <Button icon={<Plus className="size-4" />} onClick={() => setCreating(true)}>New buyer</Button>} />
      {error && <ErrorNote message={error} onRetry={reload} />}

      <div className="mb-5 flex flex-wrap items-center gap-3">
        <label className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-3" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search buyers" aria-label="Search buyers"
            className="h-10 w-full rounded-xl border border-line-strong bg-surface pr-3 pl-9 text-sm outline-none focus:border-brand" />
        </label>
        <div className="flex items-center gap-2 text-[13px] text-ink-2">
          Sort
          <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)} className="h-10 rounded-xl border border-line-strong bg-surface px-3 text-sm outline-none">
            <option value="overdue">Most overdue</option><option value="score">Lowest score</option><option value="name">Name</option>
          </select>
        </div>
      </div>

      {loading && !data ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-40" />)}</div>
      ) : rows.length === 0 ? (
        <Card><EmptyState icon={<Users className="size-6" />} title={data?.length ? "No buyers match" : "No buyers yet"} body="Add the businesses that owe you money." action={!data?.length && <Button onClick={() => setCreating(true)}>Add a buyer</Button>} /></Card>
      ) : (
        <motion.div variants={stagger.container} initial="hidden" animate="show" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {rows.map((b) => (
            <motion.div key={b.id} variants={stagger.item} layout>
              <Link href={`/app/buyers/${b.id}`} className="group block h-full">
                <Card className="h-full p-5 transition-all group-hover:-translate-y-0.5 group-hover:shadow-float">
                  <div className="flex items-start gap-4">
                    <ScoreRing score={b.score?.value ?? 0} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[15px] font-semibold tracking-tight group-hover:text-brand">{b.name}</div>
                      <div className="mt-0.5 truncate text-[12.5px] text-ink-3">{[b.code, b.city, b.profile === "small_trader" ? "Small trader" : "Company"].filter(Boolean).join(" · ")}</div>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        <Pill tone="neutral" icon={false}>{b.score?.confidence ?? "low"} confidence</Pill>
                        {b.opted_out && <Pill tone="serious"><BellOff className="sr-only" />Opted out</Pill>}
                      </div>
                    </div>
                  </div>
                  <div className="mt-4 grid grid-cols-2 gap-3 border-t border-line pt-4 text-[12.5px]">
                    <div><div className="text-ink-3">Outstanding</div><div className="tnum mt-0.5 font-mono text-[14px]">{rupees(b.outstanding_paise ?? 0)}</div></div>
                    <div><div className="text-ink-3">Overdue</div><div className="tnum mt-0.5 font-mono text-[14px] text-critical-ink">{b.overdue_paise ? rupees(b.overdue_paise) : "—"}</div>
                      {!!b.overdue_count && <div className="text-[11.5px] text-ink-3">{plural(b.overdue_count, "invoice")}</div>}</div>
                  </div>
                </Card>
              </Link>
            </motion.div>
          ))}
        </motion.div>
      )}

      <BuyerForm open={creating} onClose={() => setCreating(false)} onSaved={reload} />
    </>
  );
}

export default function BuyersPage() {
  return <Suspense><BuyersInner /></Suspense>;
}
