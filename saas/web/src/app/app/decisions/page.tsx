"use client";

import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import { Check, Copy, Gavel, Hand, Hourglass, MessageSquareText, PauseCircle, Send, ShieldCheck, UserRound } from "lucide-react";
import { useMemo, useState } from "react";
import { InvoiceActions } from "@/components/invoice-actions";
import { Ladder } from "@/components/ladder";
import { Button, Card, Drawer, EmptyState, ErrorNote, PageHeader, Pill, Select, Skeleton, cx } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api, useApi } from "@/lib/api";
import { date, plural, rupees } from "@/lib/format";
import type { Decision, DecisionDetail, DecisionKind } from "@/lib/types";

type Group = "act" | "handoff" | "wait" | "stop";
const GROUP_OF: Record<DecisionKind, Group> = { send: "act", payment_plan: "act", counter_settle: "act", handoff: "handoff", wait: "wait", stop: "stop" };
const GROUPS: { key: Group; label: string; icon: typeof Send }[] = [
  { key: "act", label: "To send", icon: Send },
  { key: "handoff", label: "For a person", icon: UserRound },
  { key: "wait", label: "Waiting", icon: Hourglass },
  { key: "stop", label: "Stopped", icon: PauseCircle },
];

const KIND_LABEL: Record<DecisionKind, string> = {
  send: "Send a reminder", payment_plan: "Offer a payment plan", counter_settle: "Discuss a settlement",
  handoff: "Hand to a person", wait: "Wait", stop: "Stop",
};

function isWeekend(iso: string) {
  const day = new Date(`${iso}T00:00:00`).getDay();
  return day === 0 || day === 6;
}

export default function DecisionsPage() {
  const { data, error, loading, reload } = useApi<{ as_of: string; tally: Record<string, number>; decisions: Decision[] }>("/decisions");
  const [group, setGroup] = useState<Group>("act");
  const [openId, setOpenId] = useState<string | null>(null);

  const counts = useMemo(() => {
    const c: Record<Group, number> = { act: 0, handoff: 0, wait: 0, stop: 0 };
    data?.decisions.forEach((d) => { c[GROUP_OF[d.kind]]++; });
    return c;
  }, [data]);
  const rows = data?.decisions.filter((d) => GROUP_OF[d.kind] === group) ?? [];

  return (
    <>
      <PageHeader
        eyebrow="Rules decide · you approve"
        title="Today’s decisions"
        description="For every overdue invoice, what the rules say to do today — and exactly why. Nothing reaches a buyer until you send it."
      />
      {error && <ErrorNote message={error} onRetry={reload} />}

      <div className="mb-5 flex flex-wrap gap-1 rounded-2xl border border-line bg-surface p-1 shadow-card sm:w-fit">
        {GROUPS.map(({ key, label, icon: Icon }) => (
          <button key={key} onClick={() => setGroup(key)} className={cx("relative flex items-center gap-2 rounded-xl px-3.5 py-2 text-[13.5px] transition-colors", group === key ? "text-brand-ink" : "text-ink-2 hover:text-ink")}>
            {group === key && <motion.span layoutId="decision-tab" className="absolute inset-0 rounded-xl bg-brand" transition={{ type: "spring", stiffness: 420, damping: 34 }} />}
            <Icon className="relative size-4" />
            <span className="relative">{label}</span>
            <span className={cx("relative rounded-full px-1.5 text-[11.5px] tnum", group === key ? "bg-white/20" : "bg-surface-2")}>{counts[key]}</span>
          </button>
        ))}
      </div>

      {loading && !data ? (
        <div className="space-y-3">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-28" />)}</div>
      ) : rows.length === 0 ? (
        <Card>
          {group === "act" && data && isWeekend(data.as_of) && counts.wait > 0 ? (
            <EmptyState icon={<Hourglass className="size-6" />} title="It’s the weekend"
              body={`Reminders never go out on a Saturday or Sunday. ${plural(counts.wait, "invoice")} are waiting and come back on Monday.`}
              action={<Button variant="secondary" onClick={() => setGroup("wait")}>See what’s waiting</Button>} />
          ) : (
            <EmptyState icon={<ShieldCheck className="size-6" />} title="Nothing here"
              body={group === "act" ? "No reminders are due today. Spacing, promises and weekends are all respected." : "No invoices in this group today."} />
          )}
        </Card>
      ) : (
        <motion.ul layout className="space-y-3">
          <AnimatePresence initial={false}>
            {rows.map((d, i) => (
              <motion.li key={d.invoice_id} layout initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0, transition: { delay: Math.min(i, 10) * 0.035 } }} exit={{ opacity: 0, x: 30 }}>
                <DecisionCard d={d} onOpen={() => setOpenId(d.invoice_id)} />
              </motion.li>
            ))}
          </AnimatePresence>
        </motion.ul>
      )}

      <DecisionDrawer invoiceId={openId} onClose={() => setOpenId(null)} onChanged={reload} />
    </>
  );
}

function kindTone(kind: DecisionKind) {
  return kind === "handoff" ? "serious" : kind === "wait" ? "neutral" : kind === "stop" ? "neutral" : "brand";
}

function DecisionCard({ d, onOpen }: { d: Decision; onOpen: () => void }) {
  return (
    <button onClick={onOpen} className="group block w-full text-left">
      <Card className="p-5 transition-all group-hover:-translate-y-0.5 group-hover:border-line-strong group-hover:shadow-float">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[15px] font-semibold tracking-tight">{d.buyer.name}</span>
              <span className="text-[13px] text-ink-3">{d.invoice_number}</span>
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              <Pill tone={kindTone(d.kind)}>{KIND_LABEL[d.kind]}{d.kind === "send" || d.kind === "payment_plan" || d.kind === "counter_settle" ? ` · rung ${d.rung}` : ""}</Pill>
              {d.days_overdue > 0 && <Pill tone={d.days_overdue >= 60 ? "critical" : "warning"}>{d.days_overdue} days overdue</Pill>}
            </div>
          </div>
          <div className="text-right">
            <div className="tnum font-mono text-[18px]">{rupees(d.outstanding_paise)}</div>
            {d.interest_paise > 0 && <div className="tnum text-[12px] text-ink-3">+ {rupees(d.interest_paise)} interest</div>}
          </div>
        </div>
        <p className="mt-3 line-clamp-2 text-[13.5px] leading-relaxed text-ink-2">{d.reason}</p>
      </Card>
    </button>
  );
}

function DecisionDrawer({ invoiceId, onClose, onChanged }: { invoiceId: string | null; onClose: () => void; onChanged: () => void }) {
  const { data, loading, reload } = useApi<DecisionDetail>(invoiceId ? `/decisions/${invoiceId}` : null);
  const toast = useToast();
  const [channel, setChannel] = useState("phone");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const d = invoiceId && data?.invoice_id === invoiceId ? data : null;
  const canSend = d && (d.kind === "send" || d.kind === "payment_plan" || d.kind === "counter_settle");

  async function approve() {
    if (!d) return;
    setBusy(true);
    try {
      await api(`/decisions/${d.invoice_id}/approve`, { method: "POST", json: { channel } });
      toast(`Rung ${d.rung} reminder logged as sent`);
      onChanged();
      onClose();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not log it", "bad");
    } finally {
      setBusy(false);
    }
  }

  async function copyDraft() {
    if (!d?.draft) return;
    await navigator.clipboard.writeText(`${d.draft.subject}\n\n${d.draft.body}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  }

  return (
    <Drawer
      open={invoiceId !== null}
      onClose={onClose}
      title={d ? d.buyer.name : "Loading…"}
      subtitle={d ? <Link href={`/app/invoices/${d.invoice_id}`} className="hover:text-brand">{d.invoice_number} · {rupees(d.outstanding_paise)} outstanding</Link> : null}
      footer={canSend ? (
        <div className="flex flex-wrap items-end justify-between gap-3">
          <Select label="I sent it by" value={channel} onChange={(e) => setChannel(e.target.value)} className="w-44">
            <option value="phone">Phone call</option>
            <option value="email">My own email</option>
            <option value="whatsapp">My own WhatsApp</option>
            <option value="in_person">In person</option>
          </Select>
          <Button onClick={() => void approve()} loading={busy} icon={<Check className="size-4" />}>Mark as sent</Button>
        </div>
      ) : undefined}
    >
      {!d || loading ? (
        <div className="space-y-3"><Skeleton className="h-24" /><Skeleton className="h-40" /><Skeleton className="h-32" /></div>
      ) : (
        <div className="space-y-6">
          <section className="rounded-2xl border border-brand/25 bg-brand-soft/60 p-4">
            <div className="flex items-center gap-2 text-[12px] font-semibold tracking-wide text-brand uppercase"><Gavel className="size-3.5" /> The decision</div>
            <div className="mt-1.5 text-[17px] font-semibold tracking-tight">{KIND_LABEL[d.kind]}{canSend ? ` — rung ${d.rung} (${d.rung_name.replace(/_/g, " ")})` : ""}</div>
            <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink-2">{d.reason}</p>
            {d.next_review_date && <p className="mt-2 text-[12.5px] text-ink-3">Next review {date(d.next_review_date)}</p>}
          </section>

          {d.rung > 0 && <section><h3 className="mb-2 text-[13px] font-semibold text-ink-2">Escalation ladder</h3><Ladder rung={d.rung} ceiling={d.available_rung} /></section>}

          {d.draft && (
            <section>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="flex items-center gap-2 text-[13px] font-semibold text-ink-2"><MessageSquareText className="size-4" /> Draft message <span className="font-normal text-ink-3">({d.draft.language})</span></h3>
                <Button size="sm" variant="secondary" onClick={() => void copyDraft()} icon={copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}>{copied ? "Copied" : "Copy"}</Button>
              </div>
              <div className="rounded-2xl border border-line bg-surface p-4">
                <div className="text-[13.5px] font-semibold">{d.draft.subject}</div>
                <pre className="mt-3 font-sans text-[13.5px] leading-relaxed whitespace-pre-wrap text-ink-2">{d.draft.body}</pre>
              </div>
              <p className="mt-2 text-[12px] text-ink-3">Every figure is checked against the law engine before it is shown. Email and WhatsApp sending aren’t connected yet — copy it and send it yourself.</p>
            </section>
          )}

          {d.samadhaan && (
            <section className="rounded-2xl border border-line p-4">
              <h3 className="flex items-center gap-2 text-[13px] font-semibold"><Hand className="size-4" /> MSME Samadhaan draft</h3>
              <p className="mt-1 text-[13px] text-ink-2">{d.samadhaan.ready ? "Ready to file, if you decide to." : "Not ready to file yet:"}</p>
              {!d.samadhaan.ready && <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-[12.5px] text-ink-2">{d.samadhaan.blockers.map((b) => <li key={b}>{b}</li>)}</ul>}
            </section>
          )}

          <section>
            <h3 className="mb-2 text-[13px] font-semibold text-ink-2">Legal position</h3>
            <dl className="grid grid-cols-2 gap-3 text-[13px]">
              {[
                ["Statutory due date", date(d.legal.statutory_due_date)],
                ["Days overdue", String(d.legal.days_overdue)],
                ["Interest to date", rupees(d.legal.interest_paise)],
                ["Accruing per day", rupees(d.legal.interest_per_day_paise, true)],
                ["Buyer’s tax exposure", rupees(d.legal.buyer_tax_exposure_paise)],
                ["Total payable", rupees(d.legal.total_payable_paise)],
              ].map(([k, v]) => (
                <div key={k} className="rounded-xl bg-surface-2 px-3 py-2.5"><dt className="text-[11.5px] text-ink-3">{k}</dt><dd className="tnum mt-0.5 font-medium">{v}</dd></div>
              ))}
            </dl>
            {d.legal.agreed_term_void && <p className="mt-2 text-[12.5px] text-ink-3">The agreed payment term was longer than the law allows, so the statutory date is {plural(d.legal.days_gained_by_law, "day")} earlier than the contract said.</p>}
          </section>

          <section>
            <h3 className="mb-2 text-[13px] font-semibold text-ink-2">Buyer score · {d.score.value}/100 <span className="font-normal text-ink-3">({d.score.confidence} confidence)</span></h3>
            <ul className="space-y-1.5 text-[12.5px]">
              {d.score.breakdown.map((b) => (
                <li key={b.factor + b.detail} className="flex justify-between gap-3 rounded-lg bg-surface-2 px-3 py-2">
                  <span className="text-ink-2">{b.detail}</span>
                  <span className={cx("tnum shrink-0 font-mono", b.points < 0 ? "text-critical-ink" : b.points > 0 ? "text-good-ink" : "text-ink-3")}>{b.points > 0 ? "+" : ""}{Math.round(b.points * 10) / 10}</span>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h3 className="mb-2 text-[13px] font-semibold text-ink-2">Something happened?</h3>
            <InvoiceActions invoiceId={d.invoice_id} outstandingPaise={d.outstanding_paise} disputed={d.kind === "handoff" && d.legal.dispute_hold}
              onDone={() => { onChanged(); void reload(); }} />
          </section>
        </div>
      )}
    </Drawer>
  );
}
