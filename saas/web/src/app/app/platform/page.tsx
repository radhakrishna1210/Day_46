"use client";

import { motion } from "motion/react";
import { Building2, Mail, ShieldCheck, Users } from "lucide-react";
import { Card, CardHeader, EmptyState, ErrorNote, PageHeader, Pill, Skeleton, stagger } from "@/components/ui";
import { useApi } from "@/lib/api";
import { date, dateTime, plural } from "@/lib/format";

type Overview = {
  totals: { businesses: number; users: number; buyers: number; invoices: number };
  businesses: {
    id: string; name: string; created_at: string | null; owner_email: string | null;
    members: number; buyers: number; invoices: number; udyam_registered: boolean; last_activity: string | null;
  }[];
  users: {
    id: string; name: string; email: string; created_at: string | null; email_verified: boolean;
    has_password: boolean; google_linked: boolean; businesses: number; is_super_admin: boolean;
  }[];
  email: { sent: number; queued: number; failed: number; recent_failures: { to: string; kind: string; at: string | null; error: string }[] };
};

function Stat({ label, value, icon: Icon }: { label: string; value: number; icon: typeof Users }) {
  return (
    <motion.div variants={stagger.item}>
      <Card className="p-5">
        <div className="flex items-center gap-2 text-[13px] text-ink-3"><Icon className="size-4" aria-hidden />{label}</div>
        <div className="mt-2 text-3xl font-semibold tracking-tight tabular-nums">{value.toLocaleString("en-IN")}</div>
      </Card>
    </motion.div>
  );
}

export default function PlatformPage() {
  const { data, error, loading, reload } = useApi<Overview>("/platform/overview");

  if (error) {
    return (
      <>
        <PageHeader eyebrow="Platform" title="Not available" />
        <ErrorNote message={error} onRetry={reload} />
      </>
    );
  }

  return (
    <>
      <PageHeader eyebrow="Platform · super admin" title="Everyone on Recova"
        description="Every business and user on this Recova server. Counts only: no buyer, invoice or message from any business is shown here." />

      {loading && !data ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24" />)}</div>
      ) : data && (
        <>
          <motion.div variants={stagger.container} initial="hidden" animate="show" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Businesses" value={data.totals.businesses} icon={Building2} />
            <Stat label="Users" value={data.totals.users} icon={Users} />
            <Stat label="Buyers tracked" value={data.totals.buyers} icon={Users} />
            <Stat label="Invoices tracked" value={data.totals.invoices} icon={ShieldCheck} />
          </motion.div>

          <Card className="mt-6">
            <CardHeader title="Businesses" subtitle={plural(data.businesses.length, "business", "businesses")} />
            {!data.businesses.length ? (
              <EmptyState icon={<Building2 className="size-6" />} title="No businesses yet" body="They appear here as people sign up." />
            ) : (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[720px] text-left text-[13.5px]">
                  <thead className="border-y border-line bg-surface-2 text-[12px] text-ink-3">
                    <tr>
                      <th className="px-5 py-2.5 font-medium">Business</th>
                      <th className="px-3 py-2.5 font-medium">Owner</th>
                      <th className="px-3 py-2.5 text-right font-medium">Team</th>
                      <th className="px-3 py-2.5 text-right font-medium">Buyers</th>
                      <th className="px-3 py-2.5 text-right font-medium">Invoices</th>
                      <th className="px-3 py-2.5 font-medium">Udyam</th>
                      <th className="px-5 py-2.5 font-medium">Last activity</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {data.businesses.map((b) => (
                      <tr key={b.id}>
                        <td className="px-5 py-3">
                          <div className="font-medium">{b.name}</div>
                          <div className="text-[12px] text-ink-3">Joined {date(b.created_at)}</div>
                        </td>
                        <td className="px-3 py-3 text-ink-2">{b.owner_email ?? "—"}</td>
                        <td className="px-3 py-3 text-right tabular-nums">{b.members}</td>
                        <td className="px-3 py-3 text-right tabular-nums">{b.buyers}</td>
                        <td className="px-3 py-3 text-right tabular-nums">{b.invoices}</td>
                        <td className="px-3 py-3">{b.udyam_registered ? <Pill tone="good">Registered</Pill> : <Pill tone="warning">Missing</Pill>}</td>
                        <td className="px-5 py-3 text-ink-2">{b.last_activity ? dateTime(b.last_activity) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card className="mt-6">
            <CardHeader title="Users" subtitle={plural(data.users.length, "account")} />
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-[13.5px]">
                <thead className="border-y border-line bg-surface-2 text-[12px] text-ink-3">
                  <tr>
                    <th className="px-5 py-2.5 font-medium">Person</th>
                    <th className="px-3 py-2.5 font-medium">Signs in with</th>
                    <th className="px-3 py-2.5 font-medium">Email</th>
                    <th className="px-3 py-2.5 text-right font-medium">Businesses</th>
                    <th className="px-5 py-2.5 font-medium">Joined</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {data.users.map((u) => (
                    <tr key={u.id}>
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-2 font-medium">
                          {u.name}
                          {u.is_super_admin && <Pill tone="brand">Super admin</Pill>}
                        </div>
                        <div className="text-[12px] text-ink-3">{u.email}</div>
                      </td>
                      <td className="px-3 py-3 text-ink-2">
                        {[u.has_password && "Password", u.google_linked && "Google", "Email code"].filter(Boolean).join(" · ")}
                      </td>
                      <td className="px-3 py-3">{u.email_verified ? <Pill tone="good">Verified</Pill> : <Pill tone="warning">Unverified</Pill>}</td>
                      <td className="px-3 py-3 text-right tabular-nums">{u.businesses}</td>
                      <td className="px-5 py-3 text-ink-2">{date(u.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card className="mt-6 mb-2">
            <CardHeader title="Email delivery" subtitle="Sign-in codes, verification and resets sent by this server" />
            <div className="flex flex-wrap gap-2 px-5 pt-3 pb-5">
              <Pill tone="good">{data.email.sent} sent</Pill>
              <Pill tone="neutral">{data.email.queued} queued</Pill>
              <Pill tone={data.email.failed ? "critical" : "neutral"}>{data.email.failed} failed</Pill>
            </div>
            {data.email.recent_failures.length > 0 && (
              <ul className="divide-y divide-line border-t border-line">
                {data.email.recent_failures.map((f, i) => (
                  <li key={i} className="flex gap-3 px-5 py-3 text-[13px]">
                    <Mail className="mt-0.5 size-4 shrink-0 text-ink-3" aria-hidden />
                    <div className="min-w-0">
                      <div className="font-medium">{f.to} · {f.kind.replace(/_/g, " ")}</div>
                      <div className="truncate text-ink-3">{f.error}</div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
    </>
  );
}
