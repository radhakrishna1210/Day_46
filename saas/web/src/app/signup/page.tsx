"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AuthShell } from "@/components/auth-shell";
import { GoogleButton } from "@/components/google-button";
import { Button, ErrorNote, Field } from "@/components/ui";
import { api } from "@/lib/api";

export default function SignupPage() {
  const router = useRouter();
  const [form, setForm] = useState({ name: "", email: "", password: "", business_name: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/auth/register", { method: "POST", json: form });
      router.push("/app?welcome=1");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the account");
      setBusy(false);
    }
  }

  return (
    <AuthShell
      title="Start recovering"
      subtitle="One account per person. Your business gets its own private workspace."
      footer={<>Already have an account? <Link href="/login" className="font-medium text-brand hover:underline">Sign in</Link></>}
    >
      <GoogleButton label="Sign up with Google" />
      <form onSubmit={submit} className="space-y-4">
        {error && <ErrorNote message={error} />}
        <Field label="Your name" autoComplete="name" required value={form.name} onChange={set("name")} />
        <Field label="Business name" required value={form.business_name} onChange={set("business_name")} placeholder="e.g. Shree Ganesh Fabricators" />
        <Field label="Work email" type="email" autoComplete="email" required value={form.email} onChange={set("email")} />
        <Field label="Password" type="password" autoComplete="new-password" required minLength={8} value={form.password} onChange={set("password")} hint="At least 8 characters." />
        <Button type="submit" size="lg" className="w-full" loading={busy}>Create account</Button>
        <p className="text-center text-[12px] text-ink-3">We’ll email you a code to confirm your address.</p>
      </form>
    </AuthShell>
  );
}
