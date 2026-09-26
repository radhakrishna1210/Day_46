"use client";

import { Check, Copy, CreditCard, RefreshCw } from "lucide-react";
import { useState } from "react";
import { useRole } from "@/components/session";
import { Button, Card, Pill } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { dateTime, rupees } from "@/lib/format";
import type { InvoiceDetail } from "@/lib/types";

/** Razorpay pay-online link for one invoice: make it, copy it, and ask
 *  Razorpay whether it was paid (a paid link records the payment itself). */
export function PaymentLinkCard({ invoiceId, link, disputed, outstanding, onChanged }: {
  invoiceId: string; link: InvoiceDetail["payment_link"]; disputed: boolean; outstanding: number; onChanged: () => void;
}) {
  const { canWrite } = useRole();
  const toast = useToast();
  const [busy, setBusy] = useState<"make" | "check" | null>(null);
  const [copied, setCopied] = useState(false);
  const open = link?.status === "created";
  const stale = open && link.amount_paise !== outstanding;

  async function run(kind: "make" | "check") {
    setBusy(kind);
    try {
      const r = await api<{ link_status?: string }>(`/invoices/${invoiceId}/payment-link${kind === "check" ? "/check" : ""}`, { method: "POST" });
      toast(kind === "make" ? "Payment link ready" : r.link_status === "paid" ? "Paid — payment recorded" : "Not paid yet");
      onChanged();
    } catch (e) {
      toast(e instanceof Error ? e.message : "That didn’t work", "bad");
    } finally {
      setBusy(null);
    }
  }

  async function copy() {
    if (!link) return;
    await navigator.clipboard.writeText(link.url);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  }

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-[15px] font-semibold tracking-tight"><CreditCard className="size-4 text-ink-3" /> Pay online</h2>
        {link?.mode === "test" && <Pill tone="warning" icon={false}>Razorpay test mode</Pill>}
      </div>
      {link ? (
        <div className="mt-3 space-y-2 text-[13.5px]">
          <div className="flex items-center gap-2">
            {link.status === "paid" ? <Pill tone="good">Paid</Pill> : open ? <Pill tone="brand">Open</Pill> : <Pill tone="neutral">{link.status}</Pill>}
            <span className="tnum font-mono">{rupees(link.amount_paise)}</span>
            <span className="text-[12px] text-ink-3">· made {dateTime(link.created_at)}</span>
          </div>
          {open && (
            <div className="flex items-center gap-2 rounded-xl bg-surface-2 px-3 py-2">
              <a href={link.url} target="_blank" rel="noreferrer" className="min-w-0 flex-1 truncate text-brand hover:underline">{link.url}</a>
              <button onClick={() => void copy()} aria-label="Copy link" className="rounded-lg p-1 text-ink-3 hover:text-ink">
                {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
              </button>
            </div>
          )}
          {stale && <p className="text-[12.5px] text-warning-ink">The amount owed changed since this link was made; making a new one replaces it.</p>}
        </div>
      ) : (
        <p className="mt-2 text-[13px] text-ink-2">A Razorpay link for the {rupees(outstanding)} outstanding — UPI, card or netbanking. Reminders you email from Recova include it automatically.</p>
      )}
      {canWrite && (
        <div className="mt-4 flex flex-wrap gap-2">
          {outstanding > 0 && !disputed && (!open || stale) && (
            <Button size="sm" loading={busy === "make"} onClick={() => void run("make")}>{link ? "Make a new link" : "Make a payment link"}</Button>
          )}
          {open && (
            <Button size="sm" variant="secondary" loading={busy === "check"} onClick={() => void run("check")} icon={<RefreshCw className="size-3.5" />}>Check payment</Button>
          )}
        </div>
      )}
      {disputed && outstanding > 0 && <p className="mt-3 text-[12.5px] text-ink-3">Disputed invoices don’t get payment links until the dispute is resolved.</p>}
    </Card>
  );
}
