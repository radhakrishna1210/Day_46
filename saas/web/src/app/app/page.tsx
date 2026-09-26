"use client";

import Link from "next/link";
import { motion } from "motion/react";
import { ArrowUpRight, BadgeIndianRupee, CalendarClock, Database, FileWarning, Hourglass, Plus, Radar, Scale, Table2, Upload } from "lucide-react";
import { useState } from "react";
import { AgingChart, CollectionsChart } from "@/components/charts";
import { useRole, useSession } from "@/components/session";
import { AnimatedNumber, Button, Card, CardHeader, ErrorNote, PageHeader, Pill, Skeleton, cx, stagger } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api, useApi } from "@/lib/api";
import { date, plural, rupees, rupeesShort } from "@/lib/format";
import type { Dashboard } from "@/lib/types";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

export default function Overview() {
  const { session } = useSession();
  const { data, error, loading, reload } = useApi<Dashboard>("/dashboard");
  const first = session.user.name.split(" ")[0];

  if (error) return <ErrorNote message={error} onRetry={reload} />;
  if (loading && !data) return <OverviewSkeleton />;
  if (!data) return null;

  if (data.totals.open_invoices === 0 && data.totals.buyers === 0) {
    return (
      <>
        <PageHeader eyebrow={session.active_business?.name} title={`${greeting()}, ${first}.`} description="Let’s get your receivables in. Pick whichever is easiest." />
        <GetStarted onLoaded={reload} />
      </>
    );
  }

  const t = data.totals;
  return (
    <>
      <PageHeader
        eyebrow={session.active_business?.name}
        title={`${greeting()}, ${first}.`}
        description={<>As of {date(data.as_of, { weekday: "long", day: "numeric", month: "long" })}. {t.overdue_invoices > 0 ? <>You have <b className="font-semibold text-ink">{rupees(t.overdue_paise)}</b> overdue across {plural(t.overdue_invoices, "invoice")}.</> : "Nothing is overdue."}</>}
        actions={<Link href="/app/decisions"><Button icon={<ArrowUpRight className="size-4" />}>Today’s decisions</Button></Link>}
      />

      <motion.div variants={stagger.container} initial="hidden" animate="show" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi icon={BadgeIndianRupee} label="Total receivable" value={t.receivable_paise} sub={plural(t.open_invoices, "open invoice")} />
        <Kpi icon={FileWarning} label="Overdue" value={t.overdue_paise} sub={`avg ${t.avg_days_overdue} days past due`} tone="critical" />
        <Kpi icon={Scale} label="Statutory interest accrued" value={t.interest_accrued_paise} sub="MSMED Act s.16, owed by buyers" tone="brand" />
        <Kpi icon={Hourglass} label="Collected, last 12 weeks" value={t.collected_12w_paise} sub={t.disputed_paise ? `${rupeesShort(t.disputed_paise)} in dispute` : "no open disputes"} />
      </motion.div>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <ChartCard title="Receivables aging" subtitle="Overdue amount by days past the statutory due date"
          chart={<AgingChart data={data.aging} />}
          table={<MiniTable head={["Days past due", "Invoices", "Amount"]} rows={data.aging.map((a) => [a.bucket, String(a.count), rupees(a.paise)])} />} />
        <ChartCard title="Collections" subtitle="Payments received per week, last 12 weeks"
          chart={<CollectionsChart data={data.collections} />}
          table={<MiniTable head={["Week of", "Collected"]} rows={data.collections.map((w) => [date(w.week_of), rupees(w.paise)])} />} />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.4fr_1fr]">
        <TopBuyers data={data} />
        <div className="space-y-6">
          <DueSoon data={data} />
          <Warnings data={data} />
        </div>
      </div>
    </>
  );
}

function Kpi({ icon: Icon, label, value, sub, tone }: { icon: typeof Scale; label: string; value: number; sub: string; tone?: "critical" | "brand" }) {
  return (
    <motion.div variants={stagger.item}>
      <Card className="group relative h-full overflow-hidden p-5 transition-shadow hover:shadow-float">
        <div className={cx("absolute -right-6 -top-6 size-24 rounded-full opacity-0 blur-2xl transition-opacity group-hover:opacity-60",
          tone === "critical" ? "bg-critical" : "bg-brand")} />
        <div className="flex items-center gap-2 text-[13px] text-ink-2">
          <Icon className={cx("size-4", tone === "critical" ? "text-critical-ink" : "text-brand")} aria-hidden /> {label}
        </div>
        <div className="mt-3 font-mono text-[28px] tracking-tight text-ink">
          <AnimatedNumber value={value} format={(v) => rupees(v)} />
        </div>
        <div className="mt-1 text-[12.5px] text-ink-3">{sub}</div>
      </Card>
    </motion.div>
  );
}

function ChartCard({ title, subtitle, chart, table }: { title: string; subtitle: string; chart: React.ReactNode; table: React.ReactNode }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <Card>
      <CardHeader title={title} subtitle={subtitle}
        action={<button onClick={() => setAsTable((v) => !v)} className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-[12.5px] text-ink-2 hover:bg-surface-2" aria-pressed={asTable}>
          <Table2 className="size-3.5" /> {asTable ? "Chart" : "Table"}
        </button>} />
      <div className="px-3 pt-4 pb-4">{asTable ? <div className="px-2">{table}</div> : chart}</div>
    </Card>
  );
}

function MiniTable({ head, rows }: { head: string[]; rows: string[][] }) {
  return (
    <table className="w-full text-[13.5px]">
      <thead><tr className="text-left text-ink-3">{head.map((h, i) => <th key={h} className={cx("pb-2 font-medium", i > 0 && "text-right")}>{h}</th>)}</tr></thead>
      <tbody>{rows.map((r) => <tr key={r[0]} className="border-t border-line">{r.map((c, i) => <td key={i} className={cx("py-2", i > 0 && "tnum text-right")}>{c}</td>)}</tr>)}</tbody>
    </table>
  );
}

function scoreTone(score: number) {
  return score >= 80 ? "good" : score >= 50 ? "warning" : "critical";
}

function TopBuyers({ data }: { data: Dashboard }) {
  const max = Math.max(1, ...data.top_buyers.map((b) => b.overdue_paise));
  return (
    <Card>
      <CardHeader title="Who owes you most" subtitle="Overdue by buyer, with their payment score" action={<Link href="/app/buyers" className="text-[13px] font-medium text-brand hover:underline">All buyers</Link>} />
      <ul className="mt-3 divide-y divide-line px-5 pb-3">
        {data.top_buyers.length === 0 && <li className="py-8 text-center text-sm text-ink-3">No overdue buyers. Nice.</li>}
        {data.top_buyers.map((b, i) => (
          <li key={b.buyer_id} className="py-3.5">
            <div className="flex items-center justify-between gap-3">
              <Link href={`/app/buyers/${b.buyer_id}`} className="min-w-0 truncate text-[14px] font-medium hover:text-brand">{b.name}</Link>
              <span className="tnum font-mono text-[14px]">{rupees(b.overdue_paise)}</span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2">
              <motion.div className="h-full rounded-full bg-[var(--viz-3)]" initial={{ width: 0 }} animate={{ width: `${(b.overdue_paise / max) * 100}%` }} transition={{ duration: 0.9, delay: 0.1 + i * 0.07, ease: [0.16, 1, 0.3, 1] }} />
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-ink-3">
              <Pill tone={scoreTone(b.score)}>Score {b.score}</Pill>
              <span>{plural(b.invoices, "invoice")} · oldest {b.oldest_days} days · interest {rupees(b.interest_paise)}</span>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function DueSoon({ data }: { data: Dashboard }) {
  return (
    <Card>
      <CardHeader title="Coming due" subtitle="Statutory due date within 14 days" />
      <ul className="mt-2 px-5 pb-4">
        {data.due_soon.length === 0 && <li className="py-6 text-center text-sm text-ink-3">Nothing due in the next two weeks.</li>}
        {data.due_soon.map((d) => (
          <li key={d.id} className="flex items-center justify-between gap-3 py-2 text-[13.5px]">
            <Link href={`/app/invoices/${d.id}`} className="min-w-0 hover:text-brand">
              <span className="font-medium">{d.invoice_number}</span> <span className="text-ink-3">· {d.buyer}</span>
            </Link>
            <span className="flex shrink-0 items-center gap-2">
              <span className="tnum font-mono">{rupeesShort(d.outstanding_paise)}</span>
              <Pill tone={d.days_to_due <= 3 ? "warning" : "neutral"}><CalendarClock className="sr-only" />{d.days_to_due === 0 ? "today" : `in ${d.days_to_due}d`}</Pill>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Warnings({ data }: { data: Dashboard }) {
  return (
    <Card>
      <CardHeader title="Early warnings" subtitle="Not due yet, but the signs point to a late payment" />
      <ul className="mt-2 space-y-2 px-5 pb-5">
        {data.early_warnings.length === 0 && <li className="py-6 text-center text-sm text-ink-3">No warning signs right now.</li>}
        {data.early_warnings.map((w) => (
          <li key={w.invoice_number} className="rounded-xl border border-line bg-bg-raised p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[13.5px] font-medium">{w.invoice_number} <span className="font-normal text-ink-3">· {w.buyer}</span></span>
              <Pill tone={w.risk_band === "high" ? "critical" : "warning"}>{w.risk_band === "high" ? "High risk" : "Watch"}</Pill>
            </div>
            <ul className="mt-1.5 space-y-0.5 text-[12.5px] text-ink-2">
              {w.reasons.map((r) => <li key={r} className="flex gap-1.5"><Radar className="mt-0.5 size-3 shrink-0 text-ink-3" />{r}</li>)}
            </ul>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function GetStarted({ onLoaded }: { onLoaded: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  async function loadDemo() {
    setBusy(true);
    try {
      const r = await api<{ buyers: number; invoices: number }>("/business/demo-data", { method: "POST" });
      toast(`Loaded ${r.buyers} buyers and ${r.invoices} invoices`);
      onLoaded();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not load demo data", "bad");
      setBusy(false);
    }
  }
  const { canWrite, canManage } = useRole();
  const options = [
    canManage && { icon: Database, title: "Load a demo book", body: "20 buyers and their invoice history, synthetic, dated to today — see Recova work in a minute.", action: <Button onClick={() => void loadDemo()} loading={busy}>Load demo data</Button> },
    canWrite && { icon: Upload, title: "Import a CSV", body: "Export invoices from Tally, Zoho or Excel and bring them in, buyers and all.", action: <Link href="/app/import"><Button variant="secondary">Import invoices</Button></Link> },
    canWrite && { icon: Plus, title: "Add by hand", body: "Start with one buyer and their open invoices.", action: <Link href="/app/buyers?new=1"><Button variant="secondary">Add a buyer</Button></Link> },
  ].filter((o) => !!o);
  if (!options.length) {
    return <Card className="p-6 text-[14px] text-ink-2">Nothing here yet. Once the team adds buyers and invoices, they appear here — you have view-only access.</Card>;
  }
  return (
    <motion.div variants={stagger.container} initial="hidden" animate="show" className="grid gap-4 md:grid-cols-3">
      {options.map(({ icon: Icon, title, body, action }) => (
        <motion.div key={title} variants={stagger.item}>
          <Card className="flex h-full flex-col p-6">
            <div className="mb-4 grid size-11 place-items-center rounded-xl bg-brand-soft text-brand"><Icon className="size-5" /></div>
            <h3 className="text-[16px] font-semibold tracking-tight">{title}</h3>
            <p className="mt-1.5 flex-1 text-[14px] text-ink-2">{body}</p>
            <div className="mt-5">{action}</div>
          </Card>
        </motion.div>
      ))}
    </motion.div>
  );
}

function OverviewSkeleton() {
  return (
    <div>
      <Skeleton className="h-11 w-80" />
      <Skeleton className="mt-3 h-5 w-96" />
      <div className="mt-8 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-32" />)}</div>
      <div className="mt-6 grid gap-6 xl:grid-cols-2"><Skeleton className="h-80" /><Skeleton className="h-80" /></div>
    </div>
  );
}
