"use client";

import { AnimatePresence, motion } from "motion/react";
import { CalendarCheck2, HandCoins, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { useRole } from "@/components/session";
import { Button, Field, Select, cx } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { rupees, toPaise } from "@/lib/format";

type Mode = "payment" | "promise" | "dispute" | null;

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
        {mode && (
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
