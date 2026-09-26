"use client";

import { AnimatePresence, motion } from "motion/react";
import { Crown, LogOut, MailPlus, RotateCw, Users, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useSession } from "@/components/session";
import { Button, Card, CardHeader, ErrorNote, Pill, Skeleton, cx } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api, useApi } from "@/lib/api";
import { date, initials } from "@/lib/format";
import type { Role, Team } from "@/lib/types";

const ROLE_HELP: Record<Exclude<Role, "owner">, string> = {
  admin: "Team, business settings, deletes",
  member: "Invoices, payments, reminders",
  viewer: "Sees everything, changes nothing",
};

const selectCls = "h-8 rounded-lg border border-line-strong bg-surface px-2 text-[12.5px] text-ink outline-none focus:border-brand";

/** Settings → Team. Every rule (who may change whom) lives on the server; this
 *  shows only the controls the server would accept. */
export function TeamCard() {
  const { data, error, reload } = useApi<Team>("/team");
  const { refresh } = useSession();
  const router = useRouter();
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Exclude<Role, "owner">>("member");
  const [busy, setBusy] = useState<string | null>(null);

  async function act(key: string, fn: () => Promise<unknown>, done: string) {
    setBusy(key);
    try {
      await fn();
      toast(done);
      await reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : "That didn’t work", "bad");
    } finally {
      setBusy(null);
    }
  }

  function invite(e: React.FormEvent) {
    e.preventDefault();
    void act("invite", async () => {
      await api("/team/invites", { method: "POST", json: { email, role } });
      setEmail("");
    }, `Invitation emailed to ${email}`);
  }

  async function leave() {
    if (!window.confirm("Leave this business? You’ll need a new invitation to come back.")) return;
    try {
      await api("/team/leave", { method: "POST" });
      await refresh();
      router.push("/app");
      toast("You left the business");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not leave", "bad");
    }
  }

  const isOwner = data?.my_role === "owner";

  return (
    <Card>
      <CardHeader title="Team" subtitle="People who can use this business" action={<Users className="size-4 text-ink-3" />} />
      {error && <div className="px-5 pt-3"><ErrorNote message={error} onRetry={reload} /></div>}
      {!data ? (
        <div className="space-y-2 p-5">{[0, 1].map((i) => <Skeleton key={i} className="h-12" />)}</div>
      ) : (
        <>
          <ul className="mt-3 space-y-1 px-3">
            {data.members.map((m) => {
              const editable = data.can_manage && m.role !== "owner" && !m.is_me;
              return (
                <li key={m.id} className="flex flex-wrap items-center gap-3 rounded-xl px-2 py-2">
                  <span className="grid size-8 shrink-0 place-items-center rounded-full bg-surface-2 text-[12px] font-semibold">{initials(m.name)}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13.5px] font-medium">{m.name}{m.is_me && <span className="font-normal text-ink-3"> (you)</span>}</span>
                    <span className="block truncate text-[12px] text-ink-3">{m.email}</span>
                  </span>
                  {editable ? (
                    <span className="flex items-center gap-1">
                      <select aria-label={`Role for ${m.name}`} className={selectCls} value={m.role} disabled={busy === m.id}
                        onChange={(e) => void act(m.id, () => api(`/team/members/${m.id}`, { method: "PUT", json: { role: e.target.value } }), `${m.name} is now ${e.target.value === "admin" ? "an" : "a"} ${e.target.value}`)}>
                        <option value="admin">Admin</option><option value="member">Member</option><option value="viewer">Viewer</option>
                      </select>
                      {isOwner && (
                        <button title={`Make ${m.name} the owner`} aria-label={`Make ${m.name} the owner`} className="rounded-lg p-1.5 text-ink-3 hover:bg-surface-2 hover:text-ink"
                          onClick={() => {
                            if (!window.confirm(`Make ${m.name} the owner? You’ll become an admin.`)) return;
                            void act(m.id, async () => { await api(`/team/members/${m.id}/make-owner`, { method: "POST" }); await refresh(); }, `${m.name} is now the owner`);
                          }}>
                          <Crown className="size-4" />
                        </button>
                      )}
                      <button title={`Remove ${m.name}`} aria-label={`Remove ${m.name}`} className="rounded-lg p-1.5 text-ink-3 hover:bg-surface-2 hover:text-critical-ink"
                        onClick={() => {
                          if (!window.confirm(`Remove ${m.name} from the team?`)) return;
                          void act(m.id, () => api(`/team/members/${m.id}`, { method: "DELETE" }), `${m.name} was removed`);
                        }}>
                        <X className="size-4" />
                      </button>
                    </span>
                  ) : (
                    <Pill tone={m.role === "owner" ? "brand" : "neutral"} icon={false}>{m.role}</Pill>
                  )}
                </li>
              );
            })}
          </ul>

          {data.can_manage && (
            <>
              <AnimatePresence initial={false}>
                {data.invites.length > 0 && (
                  <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="mt-2 border-t border-line px-5 pt-3">
                    <p className="text-[11px] font-medium tracking-wide text-ink-3 uppercase">Invited</p>
                    <ul className="mt-1">
                      {data.invites.map((i) => (
                        <li key={i.id} className="flex items-center gap-2 py-2 text-[13px]">
                          <span className="min-w-0 flex-1">
                            <span className="block truncate font-medium">{i.email}</span>
                            <span className="block text-[12px] text-ink-3">
                              {i.role} · {i.status === "expired" ? "link expired" : `link works until ${date(i.expires_at)}`}
                            </span>
                          </span>
                          <button title="Send the invitation again" aria-label={`Resend to ${i.email}`} disabled={busy === i.id}
                            className="rounded-lg p-1.5 text-ink-3 hover:bg-surface-2 hover:text-ink"
                            onClick={() => void act(i.id, () => api(`/team/invites/${i.id}/resend`, { method: "POST" }), `Sent again to ${i.email}`)}>
                            <RotateCw className={cx("size-4", busy === i.id && "animate-spin")} />
                          </button>
                          <button title="Cancel the invitation" aria-label={`Cancel invitation to ${i.email}`}
                            className="rounded-lg p-1.5 text-ink-3 hover:bg-surface-2 hover:text-critical-ink"
                            onClick={() => void act(i.id, () => api(`/team/invites/${i.id}`, { method: "DELETE" }), "Invitation cancelled")}>
                            <X className="size-4" />
                          </button>
                        </li>
                      ))}
                    </ul>
                  </motion.div>
                )}
              </AnimatePresence>

              <form onSubmit={invite} className="mt-2 space-y-2 border-t border-line p-5">
                <label className="block text-[13px] font-medium text-ink-2" htmlFor="invite-email">Invite someone</label>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <input id="invite-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@business.in"
                    className="h-10 min-w-0 flex-1 rounded-xl border border-line-strong bg-surface px-3 text-[14px] outline-none focus:border-brand" />
                  <select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value as typeof role)} className="h-10 rounded-xl border border-line-strong bg-surface px-2 text-[14px] outline-none focus:border-brand">
                    <option value="admin">Admin</option><option value="member">Member</option><option value="viewer">Viewer</option>
                  </select>
                </div>
                <p className="text-[12px] text-ink-3">{ROLE_HELP[role]}. They get an email with a link that works for 7 days.</p>
                <Button type="submit" loading={busy === "invite"} icon={<MailPlus className="size-4" />} className="w-full sm:w-auto">Send invitation</Button>
              </form>
            </>
          )}

          {!isOwner && (
            <div className="border-t border-line px-5 py-3">
              <button onClick={() => void leave()} className="flex items-center gap-2 text-[13px] text-ink-3 hover:text-critical-ink">
                <LogOut className="size-4" /> Leave this business
              </button>
            </div>
          )}
          {isOwner && <p className="border-t border-line px-5 py-3 text-[12px] text-ink-3">You’re the owner. To leave, make someone else the owner first (the crown).</p>}
        </>
      )}
    </Card>
  );
}
