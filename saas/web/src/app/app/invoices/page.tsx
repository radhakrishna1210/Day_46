"use client";

import Link from "next/link";
import { motion } from "motion/react";
import { FileText, Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { useRole } from "@/components/session";
import { StatusPill } from "@/components/status";
import { Button, Card, Drawer, EmptyState, ErrorNote, Field, PageHeader, Select, Skeleton, Toggle, cx } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api, useApi } from "@/lib/api";
import { date, rupees, toPaise } from "@/lib/format";
import { useLegal } from "@/lib/legal";
import type { BuyerRow, InvoiceRow, InvoiceStatus } from "@/lib/types";

const FILTERS: { key: InvoiceStatus | "all"; label: string }[] = [
  { key: "all", label: "All" }, { key: "overdue", label: "Overdue" }, { key: "open", label: "Not yet due" },
  { key: "disputed", label: "Disputed" }, { key: "paid", label: "Paid" },
];

export default function InvoicesPage() {
  const { data, error, loading, reload } = useApi<InvoiceRow[]>("/invoices");
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("all");
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const { canWrite } = useRole();

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (data ?? []).filter((r) =>
      (filter === "all" || r.status === filter) &&
      (!needle || r.invoice_number.toLowerCase().includes(needle) || r.buyer.name.toLowerCase().includes(needle)));
  }, [data, filter, q]);

  return (
    <>
      <PageHeader title="Invoices" description="Every invoice, with its statutory due date — counted the way the MSMED Act counts it."
        actions={canWrite && <Button icon={<Plus className="size-4" />} onClick={() => setCreating(true)}>New invoice</Button>} />
      {error && <ErrorNote message={error} onRetry={reload} />}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex flex-wrap gap-1 rounded-2xl border border-line bg-surface p-1 shadow-card">
          {FILTERS.map((f) => (
            <button key={f.key} onClick={() => setFilter(f.key)} className={cx("relative rounded-xl px-3 py-1.5 text-[13px]", filter === f.key ? "text-ink" : "text-ink-2 hover:text-ink")}>
              {filter === f.key && <motion.span layoutId="inv-filter" className="absolute inset-0 rounded-xl bg-surface-2" />}
              <span className="relative">{f.label}</span>
            </button>
          ))}
        </div>
        <label className="relative ml-auto w-full sm:w-72">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-3" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search invoice or buyer" aria-label="Search invoices"
            className="h-10 w-full rounded-xl border border-line-strong bg-surface pr-3 pl-9 text-sm outline-none focus:border-brand" />
        </label>
      </div>

      <Card className="overflow-hidden">
        {loading && !data ? (
          <div className="space-y-2 p-4">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-12" />)}</div>
        ) : rows.length === 0 ? (
          <EmptyState icon={<FileText className="size-6" />} title={data?.length ? "No invoices match" : "No invoices yet"}
            body={data?.length ? "Try another filter or search." : "Add one, or import a CSV from your accounting software."}
            action={!data?.length && <Link href="/app/import"><Button variant="secondary">Import a CSV</Button></Link>} />
        ) : (
          <>
          {/* Phones: one card per invoice. The table below takes over from sm up. */}
          <ul className="divide-y divide-line sm:hidden">
            {rows.slice(0, 300).map((r) => (
              <li key={r.id}>
                <Link href={`/app/invoices/${r.id}`} className="flex items-start justify-between gap-3 px-4 py-3.5 active:bg-bg-raised">
                  <span className="min-w-0">
                    <span className="block text-[14px] font-medium">{r.invoice_number}</span>
                    <span className="block truncate text-[13px] text-ink-2">{r.buyer.name}</span>
                    <span className="mt-1.5 block"><StatusPill row={r} /></span>
                  </span>
                  <span className="shrink-0 text-right">
                    <span className="tnum block font-mono text-[14px]">{r.outstanding_paise ? rupees(r.outstanding_paise) : "—"}</span>
                    <span className="block text-[12px] text-ink-3">due {date(r.statutory_due_date)}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          <div className="hidden overflow-x-auto sm:block">
            <table className="w-full min-w-[760px] text-[13.5px]">
              <thead className="bg-bg-raised text-left text-[12px] text-ink-3">
                <tr>
                  <th className="px-5 py-3 font-medium">Invoice</th><th className="px-3 py-3 font-medium">Buyer</th>
                  <th className="px-3 py-3 font-medium">Statutory due</th><th className="px-3 py-3 font-medium">Status</th>
                  <th className="px-3 py-3 text-right font-medium">Amount</th><th className="px-5 py-3 text-right font-medium">Outstanding</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 300).map((r, i) => (
                  <motion.tr key={r.id} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: Math.min(i, 20) * 0.015 }}
                    className="border-t border-line transition-colors hover:bg-bg-raised">
                    <td className="px-5 py-3"><Link href={`/app/invoices/${r.id}`} className="font-medium hover:text-brand">{r.invoice_number}</Link></td>
                    <td className="max-w-[240px] truncate px-3 py-3 text-ink-2"><Link href={`/app/buyers/${r.buyer.id}`} className="hover:text-brand">{r.buyer.name}</Link></td>
                    <td className="px-3 py-3 text-ink-2">{date(r.statutory_due_date)}</td>
                    <td className="px-3 py-3"><StatusPill row={r} /></td>
                    <td className="tnum px-3 py-3 text-right font-mono">{rupees(r.amount_paise)}</td>
                    <td className="tnum px-5 py-3 text-right font-mono">{r.outstanding_paise ? rupees(r.outstanding_paise) : "—"}</td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
          </div>
          </>
        )}
      </Card>

      <NewInvoice open={creating} onClose={() => setCreating(false)} onCreated={reload} />
    </>
  );
}

function NewInvoice({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => void }) {
  const buyers = useApi<BuyerRow[]>(open ? "/buyers" : null);
  const legal = useLegal();
  const toast = useToast();
  const today = new Date().toISOString().slice(0, 10);
  const [f, setF] = useState({ buyer_id: "", invoice_number: "", amount: "", issue_date: today, acceptance_date: today, written_agreement: false, agreed_days: "30", description: "", po_number: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const amount_paise = toPaise(f.amount);
    if (!f.buyer_id) return setErr("Choose a buyer");
    if (!amount_paise) return setErr("Enter the invoice amount");
    setBusy(true);
    setErr(null);
    try {
      await api("/invoices", { method: "POST", json: {
        buyer_id: f.buyer_id, invoice_number: f.invoice_number, amount_paise,
        issue_date: f.issue_date, acceptance_date: f.acceptance_date,
        written_agreement: f.written_agreement, agreed_days: f.written_agreement ? Number(f.agreed_days) : null,
        description: f.description || null, po_number: f.po_number || null } });
      toast(`Invoice ${f.invoice_number} added`);
      onCreated();
      onClose();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : "Could not add the invoice");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer open={open} onClose={onClose} title="New invoice" subtitle="The statutory due date is worked out for you.">
      <form onSubmit={submit} className="space-y-4">
        {err && <ErrorNote message={err} />}
        <Select label="Buyer" value={f.buyer_id} onChange={(e) => setF({ ...f, buyer_id: e.target.value })} required>
          <option value="">Choose a buyer…</option>
          {buyers.data?.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </Select>
        {buyers.data?.length === 0 && <p className="text-[12.5px] text-ink-3">No buyers yet — <Link href="/app/buyers?new=1" className="text-brand">add one first</Link>.</p>}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Invoice number" required value={f.invoice_number} onChange={(e) => setF({ ...f, invoice_number: e.target.value })} />
          <Field label="Amount (₹)" inputMode="decimal" required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />
          <Field label="Issue date" type="date" required value={f.issue_date} onChange={(e) => setF({ ...f, issue_date: e.target.value })} />
          <Field label="Goods accepted on" type="date" required value={f.acceptance_date} onChange={(e) => setF({ ...f, acceptance_date: e.target.value })} hint="The clock starts here." />
        </div>
        <Toggle checked={f.written_agreement} onChange={(v) => setF({ ...f, written_agreement: v })} label="There is a written payment agreement"
          hint={legal ? `Without one the law gives ${legal.no_agreement_days} days. With one, the agreed term — but never more than ${legal.max_agreement_days} days.`
            : "The law sets a shorter term without one, and caps the agreed term with one."} />
        {f.written_agreement && <Field label="Agreed payment term (days)" inputMode="numeric" value={f.agreed_days} onChange={(e) => setF({ ...f, agreed_days: e.target.value })} />}
        <Field label="Description (optional)" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
        <Field label="PO number (optional)" value={f.po_number} onChange={(e) => setF({ ...f, po_number: e.target.value })} />
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" loading={busy}>Add invoice</Button>
        </div>
      </form>
    </Drawer>
  );
}
