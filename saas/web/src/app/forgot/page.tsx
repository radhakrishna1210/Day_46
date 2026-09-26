"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AuthShell } from "@/components/auth-shell";
import { useProviders } from "@/components/google-button";
import { Button, ErrorNote, Field } from "@/components/ui";
import { api } from "@/lib/api";

export default function ForgotPage() {
  const router = useRouter();
  const providers = useProviders();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (!sent) {
        await api("/auth/code/request", { method: "POST", json: { email, purpose: "reset" } });
        setSent(true);
      } else {
        await api("/auth/password/reset", { method: "POST", json: { email, code, new_password: password } });
        router.push("/app");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Reset your password" subtitle="We’ll email you a 6-digit code."
      footer={<Link href="/login" className="font-medium text-brand hover:underline">Back to sign in</Link>}>
      <form onSubmit={submit} className="space-y-4">
        {error && <ErrorNote message={error} />}
        {sent && (
          <p className="rounded-xl bg-brand-soft px-4 py-3 text-[13px] text-brand">
            {providers?.email_delivery === "log"
              ? "Email isn’t configured on this server yet — the code is printed in the API log."
              : `If ${email} has an account, a code is on its way.`}
          </p>
        )}
        <Field label="Work email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} disabled={sent} />
        {sent && (
          <>
            <Field label="6-digit code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} required value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} className="[&_input]:font-mono [&_input]:tracking-[0.5em]" />
            <Field label="New password" type="password" autoComplete="new-password" minLength={8} required value={password}
              onChange={(e) => setPassword(e.target.value)} hint="At least 8 characters." />
          </>
        )}
        <Button type="submit" size="lg" className="w-full" loading={busy}>{sent ? "Set new password" : "Send reset code"}</Button>
      </form>
    </AuthShell>
  );
}
