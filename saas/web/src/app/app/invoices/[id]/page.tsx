"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { motion } from "motion/react";
import { ArrowLeft, CalendarCheck2, HandCoins, Mail, MessageSquareText, ShieldAlert, Trash2 } from "lucide-react";
import { InvoiceActions } from "@/components/invoice-actions";
import { Ladder } from "@/components/ladder";
import { useRole } from "@/components/session";
import { StatusPill } from "@/components/status";
import { Button, Card, CardHeader, ErrorNote, Pill, Skeleton } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api, useApi } from "@/lib/api";
import { date, plural, rupees } from "@/lib/format";
import type { DecisionDetail, InvoiceDetail } from "@/lib/types";

type Event = { at: string; icon: typeof HandCoins; title: string; body?: string; tone: "good" | "warning" | "serious" | "brand" | "neutral" };

export default function InvoicePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const inv = useApi<InvoiceDetail>(`/invoices/${id}`);
  const decision = useApi<DecisionDetail>(`/decisions/${id}`);
  const { canManage } = useRole();
  const reload = () => { void inv.reload(); void decision.reload(); };

  if (inv.error) return <ErrorNote message={inv.error} onRetry={inv.reload} />;
  if (!inv.data) return <div className="space-y-4"><Skeleton className="h-10 w-72" /><Skeleton className="h-44" /><Skeleton className="h-72" /></div>;
  const d = inv.data;
  const paidPct = Math.min(100, Math.round((d.paid_paise / d.amount_paise) * 100));

  const events: Event[] = [
    { at: d.acceptance_date, icon: CalendarCheck2, title: "Goods accepted", body: `Invoice issued ${date(d.issue_date)}`, tone: "neutral" as const },
    ...d.payments.map((p) => ({ at: p.paid_on, icon: HandCoins, title: `${rupees(p.amount_paise)} received`, body: p.note ?? undefined, tone: "good" as const })),
    ...d.promises.map((p) => ({ at: p.recorded_on, icon: CalendarCheck2, title: `Promised ${p.amount === "full" ? "full payment" : "part payment"} by ${date(p.promised_date)}`, body: `Promise ${p.status}`, tone: (p.status === "broken" ? "serious" : p.status === "kept" ? "good" : "brand") as Event["tone"] })),
    ...d.contacts.map((c) => ({ at: c.contacted_on, icon: Mail, title: `Rung ${c.rung} reminder sent`, body: `via ${c.channel.replace("_", " ")}`, tone: "brand" as const })),
    ...d.replies.map((r) => ({
      at: r.received_on, icon: MessageSquareText,
      title: `Buyer replied (${r.channel.replace("_", " ")}) — ${r.intent === "noise" ? "nothing actionable" : r.intent}`,
      body: `“${r.text}”${r.suggested_intent && r.suggested_intent !== r.intent ? ` · ${r.suggested_by === "ai" ? "AI" : "rules"} said ${r.suggested_intent}, ${r.recorded_by} corrected it` : ""}`,
      tone: (r.intent === "dispute" ? "serious" : r.intent === "promise" ? "brand" : "neutral") as Event["tone"],
    })),
  ].sort((a, b) => b.at.localeCompare(a.at));

  async function remove() {
    if (!window.confirm(`Delete invoice ${d.invoice_number}? This is recorded in the audit trail.`)) return;
    try {
      await api(`/invoices/${id}`, { method: "DELETE" });
      toast("Invoice deleted");
      router.push("/app/invoices");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not delete", "bad");
    }
  }

  return (
    <>
      <Link href="/app/invoices" className="mb-5 inline-flex items-center gap-1.5 text-[13px] text-ink-2 hover:text-ink"><ArrowLeft className="size-4" /> Invoices</Link>

      <Card className="relative overflow-hidden p-6 sm:p-8">
        <div className="absolute -right-20 -top-24 size-72 rounded-full bg-brand opacity-[0.07] blur-3xl" />
        <div className="relative flex flex-wrap items-start justify-between gap-6">
          <div>
            <div className="flex items-center gap-2"><h1 className="font-display text-[38px] leading-none tracking-tight">{d.invoice_number}</h1><StatusPill row={d} /></div>
            <Link href={`/app/buyers/${d.buyer.id}`} className="mt-2 block text-[15px] text-ink-2 hover:text-brand">{d.buyer.name}</Link>
            {d.description && <p className="mt-1 text-[13.5px] text-ink-3">{d.description}{d.po_number ? ` · ${d.po_number}` : ""}</p>}
          </div>
          <div className="text-right">
            <div className="text-[12.5px] text-ink-3">Outstanding</div>
            <div className="tnum font-mono text-[34px] tracking-tight">{rupees(d.outstanding_paise)}</div>
            {d.legal.interest_paise > 0 && <div className="tnum text-[13px] text-critical-ink">+ {rupees(d.legal.interest_paise)} statutory interest</div>}
          </div>
        </div>
        <div className="relative mt-6">
          <div className="flex justify-between text-[12px] text-ink-3"><span>{rupees(d.paid_paise)} paid of {rupees(d.amount_paise)}</span><span>{paidPct}%</span></div>
          <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-surface-2">
            <motion.div className="h-full rounded-full bg-good" initial={{ width: 0 }} animate={{ width: `${paidPct}%` }} transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }} />
          </div>
        </div>
      </Card>

      <div className="mt-6 grid gap-6 lg:grid-cols-[1.25fr_1fr]">
        <div className="space-y-6">
          {decision.data && (
            <Card className="p-5">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-[15px] font-semibold tracking-tight">Today’s decision</h2>
                <Link href="/app/decisions" className="text-[13px] font-medium text-brand hover:underline">Open queue</Link>
              </div>
              <p className="mt-2 text-[13.5px] leading-relaxed text-ink-2">{decision.data.reason}</p>
              {decision.data.rung > 0 && <div className="mt-4"><Ladder rung={decision.data.rung} ceiling={decision.data.available_rung} /></div>}
            </Card>
          )}

          <Card className="p-5">
            <h2 className="mb-3 text-[15px] font-semibold tracking-tight">Record what happened</h2>
            <InvoiceActions invoiceId={d.id} outstandingPaise={d.outstanding_paise} disputed={d.disputed} onDone={reload} />
            {d.disputed && (
              <p className="mt-3 flex items-center gap-2 text-[13px] text-serious-ink"><ShieldAlert className="size-4" /> Disputed{d.dispute_note ? `: ${d.dispute_note}` : ""} — automated chasing is paused.</p>
            )}
          </Card>

          <Card>
            <CardHeader title="Timeline" subtitle="Everything that has happened to this invoice" />
            <ol className="relative mt-4 space-y-5 px-6 pb-6 before:absolute before:top-2 before:bottom-8 before:left-[35px] before:w-px before:bg-line">
              {events.map((e, i) => (
                <motion.li key={`${e.at}-${i}`} className="relative flex gap-4" initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.05 }}>
                  <span className="relative z-10 grid size-6 shrink-0 place-items-center rounded-full border border-line bg-surface"><e.icon className="size-3.5 text-ink-2" /></span>
                  <div className="min-w-0">
                    <div className="text-[13.5px] font-medium">{e.title}</div>
                    <div className="text-[12px] text-ink-3">{date(e.at)}{e.body ? ` · ${e.body}` : ""}</div>
                  </div>
                </motion.li>
              ))}
            </ol>
          </Card>
        </div>

        <div className="space-y-6">
          <Card className="p-5">
            <h2 className="text-[15px] font-semibold tracking-tight">Legal position</h2>
            <p className="mt-1 text-[12.5px] text-ink-3">MSMED Act 2006 · simplified, not legal advice</p>
            <dl className="mt-4 space-y-2.5 text-[13.5px]">
              {[
                ["Goods accepted", date(d.acceptance_date)],
                ["Agreed term", d.written_agreement ? plural(d.agreed_days ?? 0, "day") : "No written agreement"],
                ["Statutory due date", date(d.legal.statutory_due_date)],
                ["Interest runs from", date(d.legal.interest_from)],
                ["Days overdue", String(Math.max(0, d.legal.days_overdue))],
                ["Interest to date", rupees(d.legal.interest_paise)],
                ["Cost of waiting a week", rupees(d.legal.cost_of_waiting_paise)],
                ["Buyer’s own tax exposure", rupees(d.legal.buyer_tax_exposure_paise)],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between gap-4 border-b border-line pb-2.5 last:border-0"><dt className="text-ink-2">{k}</dt><dd className="tnum text-right font-medium">{v}</dd></div>
              ))}
            </dl>
            {d.legal.agreed_term_void && <div className="mt-3"><Pill tone="warning">Agreed term exceeds the legal ceiling</Pill></div>}
          </Card>

          {d.legal.facts.length > 0 && (
            <Card className="p-5">
              <h2 className="text-[15px] font-semibold tracking-tight">Facts you can state</h2>
              <ul className="mt-3 space-y-2 text-[13px] leading-relaxed text-ink-2">
                {d.legal.facts.map((f) => <li key={f} className="rounded-xl bg-surface-2 px-3 py-2.5">{f}</li>)}
              </ul>
            </Card>
          )}

          {canManage && <Button variant="ghost" className="text-critical-ink" icon={<Trash2 className="size-4" />} onClick={() => void remove()}>Delete invoice</Button>}
        </div>
      </div>
    </>
  );
}
