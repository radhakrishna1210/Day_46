"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthShell } from "@/components/auth-shell";
import { GoogleButton } from "@/components/google-button";
import { Button, ErrorNote, Field, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import type { InviteInfo, Session } from "@/lib/types";

const ROLE_LINE = {
  owner: "the owner",
  admin: "an admin — you’ll manage the team and business settings",
  member: "a member — you’ll work invoices, payments and reminders",
  viewer: "a viewer — you’ll see everything, and change nothing",
};

/** Where an invitation email's link lands. Works signed in or out: an
 *  invitation can only be accepted by an account with the invited email. */
export default function InvitePage() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const [invite, setInvite] = useState<InviteInfo | null>(null);
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ name: "", password: "" });
  const here = `/invite/${token}`;

  useEffect(() => {
    api<InviteInfo>(`/invites/${token}`).then(setInvite, (e: unknown) => setError(e instanceof Error ? e.message : "This invitation link isn’t valid"));
    api<Session>("/auth/session").then(setSession, () => setSession(null));
  }, [token]);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      router.push("/app?welcome=1");
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn’t work");
      setBusy(false);
    }
  }

  async function signOut() {
    await api("/auth/logout", { method: "POST" });
    setSession(null);
  }

  const title = invite ? `Join ${invite.business}` : "Invitation";
  const subtitle = invite
    ? `${invite.invited_by} invited ${invite.email} as ${ROLE_LINE[invite.role]}.`
    : "Checking your invitation…";

  let body: React.ReactNode;
  if (!invite || session === undefined) {
    body = error ? null : <div className="space-y-3"><Skeleton className="h-11" /><Skeleton className="h-11" /></div>;
  } else if (invite.status !== "pending") {
    body = (
      <p className="rounded-xl bg-surface-2 px-4 py-3 text-[14px] text-ink-2">
        {invite.status === "accepted" ? "This invitation has already been accepted." :
          invite.status === "revoked" ? "This invitation was cancelled." :
            "This invitation has expired."} Ask {invite.invited_by} to send a new one.
        {invite.status === "accepted" && <> <Link href="/app" className="font-medium text-brand hover:underline">Open Recova</Link></>}
      </p>
    );
  } else if (session && session.user.email.toLowerCase() === invite.email) {
    body = <Button size="lg" className="w-full" loading={busy} onClick={() => void run(() => api(`/invites/${token}/accept`, { method: "POST" }))}>Accept and join {invite.business}</Button>;
  } else if (session) {
    body = (
      <div className="space-y-3">
        <p className="rounded-xl bg-[color-mix(in_oklab,var(--warning)_14%,transparent)] px-4 py-3 text-[14px] text-warning-ink">
          You’re signed in as {session.user.email}, but this invitation is for {invite.email}.
        </p>
        <Button variant="secondary" size="lg" className="w-full" onClick={() => void signOut()}>Sign out and continue as {invite.email}</Button>
      </div>
    );
  } else if (invite.has_account) {
    body = (
      <div>
        <GoogleButton next={here} />
        <Link href={`/login?next=${encodeURIComponent(here)}&email=${encodeURIComponent(invite.email)}`}>
          <Button size="lg" className="w-full">Sign in as {invite.email}</Button>
        </Link>
      </div>
    );
  } else {
    body = (
      <div>
        <GoogleButton label="Join with Google" next={here} />
        <form className="space-y-4" onSubmit={(e) => {
          e.preventDefault();
          void run(() => api("/auth/register", { method: "POST", json: { ...form, email: invite.email, invite_token: token } }));
        }}>
          <Field label="Email" value={invite.email} disabled readOnly />
          <Field label="Your name" autoComplete="name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <Field label="Choose a password" type="password" autoComplete="new-password" required minLength={8} value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })} hint="At least 8 characters." />
          <Button type="submit" size="lg" className="w-full" loading={busy}>Create account and join</Button>
        </form>
      </div>
    );
  }

  return (
    <AuthShell title={title} subtitle={subtitle}
      footer={<span className="text-ink-3">Invitations only work for the email they were sent to.</span>}>
      {error && <div className="mb-4"><ErrorNote message={error} /></div>}
      {body}
    </AuthShell>
  );
}
