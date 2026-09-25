"use client";

import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import { CheckCircle2, Download, FileSpreadsheet, UploadCloud, XCircle } from "lucide-react";
import { useRef, useState } from "react";
import { Button, Card, PageHeader, cx } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { ApiError } from "@/lib/api";
import { plural } from "@/lib/format";

type Result = { created: number; new_buyers: number; skipped: { line: number; error: string }[] };

const TEMPLATE = [
  "invoice_number,buyer_name,amount_rupees,issue_date,acceptance_date,written_agreement,agreed_days,description,po_number",
  "INV-1001,Vistara Traders,125000.50,2026-07-01,2026-07-03,yes,30,Steel brackets,PO-778",
  "INV-1002,Shree Balaji Stores,48000,2026-07-12,2026-07-12,no,,Packing material,",
].join("\n");

export default function ImportPage() {
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<Result | null>(null);

  async function upload(f: File) {
    setFile(f);
    setBusy(true);
    setResult(null);
    const form = new FormData();
    form.append("file", f);
    try {
      const res = await fetch("/api/invoices/import", { method: "POST", body: form, credentials: "include" });
      const body = await res.json();
      if (!res.ok) throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : "Import failed");
      setResult(body as Result);
      toast(`Imported ${plural(body.created, "invoice")}`);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Import failed", "bad");
    } finally {
      setBusy(false);
    }
  }

  function downloadTemplate() {
    const url = URL.createObjectURL(new Blob([TEMPLATE], { type: "text/csv" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "recova-invoices-template.csv" });
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <>
      <PageHeader title="Import invoices" description="Bring your book in from Tally, Zoho, Busy or a spreadsheet. New buyers are created by name; bad rows are skipped and explained, never half-imported."
        actions={<Button variant="secondary" icon={<Download className="size-4" />} onClick={downloadTemplate}>Download template</Button>} />

      <motion.div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) void upload(f); }}
        animate={{ scale: drag ? 1.01 : 1 }}
        className={cx("relative cursor-pointer overflow-hidden rounded-3xl border-2 border-dashed p-12 text-center transition-colors",
          drag ? "border-brand bg-brand-soft/50" : "border-line-strong bg-surface hover:border-brand/60")}
        onClick={() => input.current?.click()}
        role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
      >
        <input ref={input} type="file" accept=".csv,text/csv" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
        <motion.div animate={busy ? { y: [0, -8, 0] } : { y: 0 }} transition={busy ? { repeat: Infinity, duration: 1 } : {}} className="mx-auto grid size-16 place-items-center rounded-2xl bg-brand-soft text-brand">
          <UploadCloud className="size-8" />
        </motion.div>
        <p className="mt-5 text-[17px] font-semibold tracking-tight">{busy ? `Importing ${file?.name}…` : "Drop a CSV here, or click to choose"}</p>
        <p className="mt-1 text-[13.5px] text-ink-3">Required columns: invoice_number, buyer_name, amount_rupees, issue_date, acceptance_date (YYYY-MM-DD)</p>
      </motion.div>

      <AnimatePresence>
        {result && (
          <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="mt-6 grid gap-4 md:grid-cols-3">
            <Card className="p-5"><CheckCircle2 className="size-5 text-good-ink" /><div className="tnum mt-3 font-mono text-3xl">{result.created}</div><div className="text-[13px] text-ink-2">invoices imported</div></Card>
            <Card className="p-5"><FileSpreadsheet className="size-5 text-brand" /><div className="tnum mt-3 font-mono text-3xl">{result.new_buyers}</div><div className="text-[13px] text-ink-2">new buyers created</div></Card>
            <Card className="p-5"><XCircle className={cx("size-5", result.skipped.length ? "text-critical-ink" : "text-ink-3")} /><div className="tnum mt-3 font-mono text-3xl">{result.skipped.length}</div><div className="text-[13px] text-ink-2">rows skipped</div></Card>
            {result.skipped.length > 0 && (
              <Card className="p-5 md:col-span-3">
                <h3 className="text-[14px] font-semibold">Rows that were skipped</h3>
                <ul className="mt-3 space-y-1.5 text-[13px]">
                  {result.skipped.map((s) => <li key={s.line} className="flex gap-3 rounded-lg bg-surface-2 px-3 py-2"><span className="tnum w-16 shrink-0 font-mono text-ink-3">line {s.line}</span><span className="text-ink-2">{s.error}</span></li>)}
                </ul>
              </Card>
            )}
            {result.created > 0 && <div className="md:col-span-3"><Link href="/app/decisions"><Button>See today’s decisions</Button></Link></div>}
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
