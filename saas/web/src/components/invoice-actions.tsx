"use client";

import { AnimatePresence, motion } from "motion/react";
import { AlertTriangle, CalendarCheck2, HandCoins, MessageSquareText, ShieldAlert, Sparkles } from "lucide-react";
import { useState } from "react";
import { useRole } from "@/components/session";
import { Button, Field, Select, cx } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { rupees, toPaise } from "@/lib/format";

type Mode = "reply" | "payment" | "promise" | "dispute" | null;
type Intent = "promise" | "dispute" | "refusal" | "question" | "noise";

type Suggestion = {
  intent: Intent; date: string | null; amount: "full" | "partial" | null; confidence: string;
  quote: string; reader: "ai" | "rules"; flags: string[]; downgraded?: string[];
};

const INTENTS: { key: Intent; label: string; effect: string }[] = [
  { key: "promise", label: "Promise to pay", effect: "Records the promise; reminders pause until that date." },
  { key: "dispute", label: "Dispute", effect: "Marks the invoice disputed; automated chasing stops and it goes to a person." },
  { key: "refusal", label: "Refusal", effect: "Recorded. The ladder carries on as the rules decide." },
  { key: "question", label: "Question", effect: "Recorded. Answer them yourself; the ladder carries on." },
  { key: "noise", label: "Nothing actionable", effect: "Recorded, nothing changes." },
];

/** Paste what the buyer said; the reader (AI if connected, else rules)
 *  suggests what it means; a person confirms or corrects it; only then does
 *  anything change. Both steps land in the audit trail. */
function ReplyPanel({ invoiceId, onCancel, onDone }: { invoiceId: string; onCancel: () => void; onDone: () => void }) {
  const toast = useToast();
  const [text, setText] = useState("");
  const [channel, setChannel] = useState("whatsapp");
  const [s, setS] = useState<Suggestion | null>(null);
  const [intent, setIntent] = useState<Intent>("noise");
  const [when, setWhen] = useState("");
  const [amount, setAmount] = useState<"full" | "partial">("full");
  const [busy, setBusy] = useState(false);

  async function read() {
    setBusy(true);
    try {
      const got = await api<Suggestion>(`/invoices/${invoiceId}/replies/read`, { method: "POST", json: { text } });
      setS(got);
      setIntent(got.intent);
      setWhen(got.date ?? "");
      setAmount(got.amount ?? "full");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not read that", "bad");
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    setBusy(true);
    try {
      await api(`/invoices/${invoiceId}/replies`, { method: "POST", json: {
        text, intent, channel, promised_date: intent === "promise" ? when || null : null, amount,
        suggested_intent: s?.intent ?? null, suggested_by: s?.reader ?? null } });
      toast(intent === "promise" ? "Promise recorded — reminders pause until then"
        : intent === "dispute" ? "Marked disputed — handed to a person" : "Reply recorded");
      onDone();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not save", "bad");
      setBusy(false);
    }
  }

  const chosen = INTENTS.find((i) => i.key === intent)!;
  return (
    <div className="mt-3 space-y-3 rounded-xl border border-line bg-bg-raised p-4">
      <label className="block">
        <span className="mb-1.5 block text-[13px] font-medium text-ink-2">What did the buyer say?</span>
        <textarea value={text} onChange={(e) => { setText(e.target.value); setS(null); }} rows={3} autoFocus
          placeholder="Paste their WhatsApp message or email — English or Hinglish, e.g. “5 tarikh tak ho jayega”"
          className="w-full rounded-xl border border-line-strong bg-surface px-3.5 py-2.5 text-[14px] text-ink outline-none focus:border-brand" />
      </label>
      {!s ? (
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button type="button" loading={busy} disabled={!text.trim()} onClick={() => void read()} icon={<Sparkles className="size-4" />}>Read it</Button>
        </div>
      ) : (
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="space-y-3">
          <div className="rounded-xl bg-brand-soft px-4 py-3 text-[13px] text-brand">
            <span className="font-medium">{s.reader === "ai" ? "AI" : "Rules"} read this as {INTENTS.find((i) => i.key === s.intent)?.label.toLowerCase()}</span>
            {s.date && <> · paying by {s.date}</>} · {s.confidence} confidence
            {s.reader === "rules" && <span className="mt-1 block text-[12px] opacity-80">No AI key is connected, so simple rules read it. Check it before confirming.</span>}
            {s.downgraded?.map((d) => <span key={d} className="mt-1 block text-[12px] opacity-80">Not taken at face value: {d}</span>)}
          </div>
          {s.flags.map((f) => (
            <p key={f} className="flex gap-2 rounded-xl bg-[color-mix(in_oklab,var(--warning)_14%,transparent)] px-4 py-2.5 text-[13px] text-warning-ink">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" /> {f}
            </p>
          ))}
          <div className="grid gap-3 sm:grid-cols-2">
            <Select label="It means" value={intent} onChange={(e) => setIntent(e.target.value as Intent)}>
              {INTENTS.map((i) => <option key={i.key} value={i.key}>{i.label}</option>)}
            </Select>
            <Select label="They replied by" value={channel} onChange={(e) => setChannel(e.target.value)}>
              <option value="whatsapp">WhatsApp</option><option value="email">Email</option><option value="phone">Phone call</option>
              <option value="in_person">In person</option><option value="sms">SMS</option><option value="other">Other</option>
            </Select>
            {intent === "promise" && (
              <>
                <Field label="Promised to pay by" type="date" value={when} onChange={(e) => setWhen(e.target.value)} min={today()} required />
                <Select label="Amount promised" value={amount} onChange={(e) => setAmount(e.target.value as "full" | "partial")}>
                  <option value="full">The full amount</option><option value="partial">Part of it</option>
                </Select>
              </>
            )}
          </div>
          <p className="text-[12.5px] text-ink-3">{chosen.effect}</p>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
            <Button type="button" loading={busy} disabled={intent === "promise" && !when} variant={intent === "dispute" ? "danger" : "primary"}
              onClick={() => void confirm()}>{s.intent === intent ? "Confirm" : "Save my reading"}</Button>
          </div>
        </motion.div>
      )}
    </div>
  );
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

/** Record what happened with a buyer: money in, a promise, a dispute. Each is
 *  one API call and one audit row; the engine re-decides from it. */
export function InvoiceActions(props: {
  invoiceId: string; outstandingPaise: number; disputed: boolean; onDone: () => void;
}) {
  return useRole().canWrite ? <InvoiceActionsForm {...props} /> : null;
}

function InvoiceActionsForm({ invoiceId, outstandingPaise, disputed, onDone }: {
  invoiceId: string; outstandingPaise: number; disputed: boolean; onDone: () => void;
}) {
  const [mode, setMode] = useState<Mode>(null);
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState(today());
  const [promiseKind, setPromiseKind] = useState<"full" | "partial">("full");
  const [note, setNote] = useState("");

  async function run(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await fn();
      toast(done);
      setMode(null);
      setAmount("");
      setNote("");
      onDone();
    } catch (e) {
      toast(e instanceof Error ? e.message : "That didn’t work", "bad");
    } finally {
      setBusy(false);
    }
  }

  const tabs: { key: Exclude<Mode, null>; label: string; icon: typeof HandCoins }[] = [
    { key: "reply", label: "Buyer replied", icon: MessageSquareText },
    { key: "payment", label: "Payment received", icon: HandCoins },
    { key: "promise", label: "Buyer promised", icon: CalendarCheck2 },
    { key: "dispute", label: disputed ? "Resolve dispute" : "Buyer disputes", icon: ShieldAlert },
  ];

  return (
    <div>
      <div className="flex flex-wrap gap-2">
        {tabs.map(({ key, label, icon: Icon }) => (
          <button key={key} onClick={() => setMode(mode === key ? null : key)}
            className={cx("flex items-center gap-1.5 rounded-xl border px-3 py-2 text-[13px] transition-colors",
              mode === key ? "border-brand bg-brand-soft text-brand" : "border-line-strong bg-surface text-ink-2 hover:text-ink")}>
            <Icon className="size-4" /> {label}
          </button>
        ))}
      </div>
      <AnimatePresence initial={false} mode="wait">
        {mode === "reply" ? (
          <motion.div key="reply" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
            <ReplyPanel invoiceId={invoiceId} onCancel={() => setMode(null)} onDone={() => { setMode(null); onDone(); }} />
          </motion.div>
        ) : mode && (
          <motion.form
            key={mode}
            initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
            onSubmit={(e) => {
              e.preventDefault();
              if (mode === "payment") {
                const paise = toPaise(amount);
                if (!paise) return toast("Enter the amount received", "bad");
                void run(() => api(`/invoices/${invoiceId}/payments`, { method: "POST", json: { paid_on: on, amount_paise: paise, note: note || null } }), `Recorded ${rupees(paise)}`);
              } else if (mode === "promise") {
                void run(() => api(`/invoices/${invoiceId}/promises`, { method: "POST", json: { promised_date: on, amount: promiseKind, note: note || null } }), "Promise recorded — reminders pause until then");
              } else {
                void run(() => api(`/invoices/${invoiceId}/dispute`, { method: "POST", json: { disputed: !disputed, note: note || null } }),
                  disputed ? "Dispute resolved" : "Marked disputed — handed to a person");
              }
            }}
          >
            <div className="mt-3 space-y-3 rounded-xl border border-line bg-bg-raised p-4">
              {mode === "payment" && (
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Amount (₹)" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} hint={`Outstanding ${rupees(outstandingPaise)}`} autoFocus />
                  <Field label="Received on" type="date" value={on} onChange={(e) => setOn(e.target.value)} max={today()} />
                </div>
              )}
              {mode === "promise" && (
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Promised to pay by" type="date" value={on} onChange={(e) => setOn(e.target.value)} min={today()} />
                  <Select label="Amount promised" value={promiseKind} onChange={(e) => setPromiseKind(e.target.value as "full" | "partial")}>
                    <option value="full">The full amount</option>
                    <option value="partial">Part of it</option>
                  </Select>
                </div>
              )}
              {mode === "dispute" && (
                <p className="text-[13px] text-ink-2">
                  {disputed ? "The invoice goes back onto the normal reminder ladder." : "Automated chasing stops at once, and the invoice is handed to a person — chasing a disputed invoice loses customers."}
                </p>
              )}
              <Field label="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} placeholder={mode === "dispute" ? "What is the buyer unhappy about?" : "Anything worth remembering"} />
              <div className="flex justify-end gap-2">
                <Button type="button" variant="ghost" onClick={() => setMode(null)}>Cancel</Button>
                <Button type="submit" loading={busy} variant={mode === "dispute" && !disputed ? "danger" : "primary"}>Save</Button>
              </div>
            </div>
          </motion.form>
        )}
      </AnimatePresence>
    </div>
  );
}
