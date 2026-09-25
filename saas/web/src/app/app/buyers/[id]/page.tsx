"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { motion } from "motion/react";
import { ArrowLeft, Mail, MapPin, Pencil, Phone, Trash2 } from "lucide-react";
import { useState } from "react";
import { BuyerForm } from "@/components/buyer-form";
import { ScoreRing } from "@/components/score-ring";
import { Button, Card, CardHeader, ErrorNote, Pill, Skeleton, cx } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api, useApi } from "@/lib/api";
import { rupees } from "@/lib/format";
import type { BuyerRow } from "@/lib/types";

export default function BuyerPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const { data: b, error, reload } = useApi<BuyerRow>(`/buyers/${id}`);
  const [editing, setEditing] = useState(false);

  if (error) return <ErrorNote message={error} onRetry={reload} />;
  if (!b) return <div className="space-y-4"><Skeleton className="h-10 w-72" /><Skeleton className="h-48" /></div>;

  async function remove() {
    if (!b || !window.confirm(`Delete ${b.name} and all their invoices? This is recorded in the audit trail.`)) return;
    try {
      await api(`/buyers/${id}`, { method: "DELETE" });
      toast("Buyer deleted");
      router.push("/app/buyers");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not delete", "bad");
    }
  }

  const open = (b.invoices ?? []).filter((i) => i.outstanding_paise > 0);
  return (
    <>
      <Link href="/app/buyers" className="mb-5 inline-flex items-center gap-1.5 text-[13px] text-ink-2 hover:text-ink"><ArrowLeft className="size-4" /> Buyers</Link>
      <Card className="p-6 sm:p-8">
        <div className="flex flex-wrap items-start gap-6">
          <ScoreRing score={b.score?.value ?? 0} size={84} />
          <div className="min-w-0 flex-1">
            <h1 className="font-display text-[36px] leading-none tracking-tight">{b.name}</h1>
            <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 text-[13px] text-ink-2">
              {b.city && <span className="flex items-center gap-1.5"><MapPin className="size-3.5" />{[b.city, b.state].filter(Boolean).join(", ")}</span>}
              {b.contact_email && <span className="flex items-center gap-1.5"><Mail className="size-3.5" />{b.contact_email}</span>}
              {b.contact_phone && <span className="flex items-center gap-1.5"><Phone className="size-3.5" />{b.contact_phone}</span>}
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              <Pill tone="neutral" icon={false}>{b.code}</Pill>
              <Pill tone="neutral" icon={false}>{b.profile === "small_trader" ? "Small trader" : "Company"}</Pill>
              <Pill tone="neutral" icon={false}>{b.language_pref === "hinglish" ? "Hinglish" : "English"}</Pill>
              {b.opted_out && <Pill tone="serious">Opted out of contact</Pill>}
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="secondary" icon={<Pencil className="size-4" />} onClick={() => setEditing(true)}>Edit</Button>
            <Button variant="ghost" className="text-critical-ink" icon={<Trash2 className="size-4" />} onClick={() => void remove()}>Delete</Button>
          </div>
        </div>
      </Card>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title={`Payment score ${b.score?.value ?? "—"}/100`} subtitle={`${b.score?.confidence ?? "low"} confidence · from ${b.score?.history_count ?? 0} settled invoices`} />
          <ul className="mt-3 space-y-1.5 px-5 pb-5 text-[13px]">
            {b.score?.breakdown.map((row, i) => (
              <motion.li key={row.factor + i} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}
                className="flex justify-between gap-4 rounded-xl bg-surface-2 px-3 py-2.5">
                <span className="text-ink-2">{row.detail}</span>
                <span className={cx("tnum shrink-0 font-mono", row.points < 0 ? "text-critical-ink" : row.points > 0 ? "text-good-ink" : "text-ink-3")}>{row.points > 0 ? "+" : ""}{Math.round(row.points * 10) / 10}</span>
              </motion.li>
            ))}
          </ul>
        </Card>

        <Card>
          <CardHeader title="Open invoices" subtitle={`${open.length} with money still owed`} />
          <ul className="mt-2 divide-y divide-line px-5 pb-3">
            {open.length === 0 && <li className="py-8 text-center text-sm text-ink-3">Nothing owed right now.</li>}
            {open.map((i) => (
              <li key={i.id} className="flex items-center justify-between gap-3 py-3 text-[13.5px]">
                <Link href={`/app/invoices/${i.id}`} className="font-medium hover:text-brand">{i.invoice_number}</Link>
                <span className="flex items-center gap-3">
                  {i.days_overdue > 0 ? <Pill tone={i.days_overdue >= 60 ? "critical" : "warning"}>{i.days_overdue}d overdue</Pill> : <Pill tone="neutral">Not yet due</Pill>}
                  <span className="tnum font-mono">{rupees(i.outstanding_paise)}</span>
                </span>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <BuyerForm open={editing} onClose={() => setEditing(false)} onSaved={reload} buyer={b} />
    </>
  );
}
