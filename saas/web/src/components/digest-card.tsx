"use client";

import { Mail } from "lucide-react";
import { useState } from "react";
import { useRole, useSession } from "@/components/session";
import { Button, Card, CardHeader, Skeleton, Toggle } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api, useApi } from "@/lib/api";
import { date, plural, rupees } from "@/lib/format";

type Preview = {
  summary: { to_act: number; handoffs: number; overdue_invoices: number; overdue_paise: number; promises_due_today: unknown[]; promises_lapsed: number };
  would_send: boolean; recipients: string[]; last_daily_run: string | null; digest_hour: number; me_opted_out: boolean;
};

/** Settings → the morning digest: on/off for me, and what today's says. */
export function DigestCard() {
  const { session, refresh } = useSession();
  const { canWrite } = useRole();
  const toast = useToast();
  const { data, reload } = useApi<Preview>("/digest");
  const [busy, setBusy] = useState(false);
  const optedOut = session.user.digest_opt_out;

  async function toggle(on: boolean) {
    try {
      await api("/auth/preferences", { method: "PUT", json: { digest_opt_out: !on } });
      await refresh();
      await reload();
      toast(on ? "You’ll get the morning digest" : "Morning digest turned off");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not save", "bad");
    }
  }

  async function sendNow() {
    setBusy(true);
    try {
      const r = await api<{ message: string }>("/digest/send-me", { method: "POST" });
      toast(r.message);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not send", "bad");
    } finally {
      setBusy(false);
    }
  }

  const hour = data ? `${((data.digest_hour + 11) % 12) + 1} ${data.digest_hour < 12 ? "am" : "pm"}` : "";
  return (
    <Card>
      <CardHeader title="Morning digest" subtitle={data ? `Emailed after ${hour} on days there’s something to do` : "Loading…"} action={<Mail className="size-4 text-ink-3" />} />
      <div className="space-y-4 p-5">
        {canWrite ? (
          <Toggle checked={!optedOut} onChange={(v) => void toggle(v)} label="Email me the digest"
            hint={session.user.email_verified ? `To ${session.user.email}` : "Confirm your email to receive it"} />
        ) : (
          <p className="text-[13px] text-ink-3">Viewers don’t get the digest.</p>
        )}
        {!data ? <Skeleton className="h-16" /> : (
          <div className="rounded-xl bg-surface-2 px-4 py-3 text-[13px] text-ink-2">
            <p className="font-medium text-ink">Today it would say</p>
            <p className="mt-1">
              {plural(data.summary.to_act, "invoice")} to act on · {data.summary.handoffs} for a person · {plural(data.summary.promises_due_today.length, "promise")} due today
            </p>
            <p className="mt-1 text-ink-3">{rupees(data.summary.overdue_paise)} overdue across {plural(data.summary.overdue_invoices, "invoice")}.</p>
            <p className="mt-2 text-[12px] text-ink-3">
              Goes to {data.recipients.length ? data.recipients.join(", ") : "nobody yet"} · {data.last_daily_run ? `last run ${date(data.last_daily_run)}` : "not run yet today"}
            </p>
          </div>
        )}
        {canWrite && <Button variant="secondary" loading={busy} onClick={() => void sendNow()} disabled={!session.user.email_verified}>Email me today’s now</Button>}
      </div>
    </Card>
  );
}
