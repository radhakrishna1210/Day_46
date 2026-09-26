"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import { Suspense, useState } from "react";
import { AuthShell } from "@/components/auth-shell";
import { GoogleButton, useProviders } from "@/components/google-button";
import { Button, ErrorNote, Field, cx } from "@/components/ui";
import { api } from "@/lib/api";

type Mode = "password" | "code";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next");
  const providers = useProviders();
  const [mode, setMode] = useState<Mode>("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [error, setError] = useState<string | null>(params.get("error"));
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const done = () => router.push(next && next.startsWith("/app") ? next : "/app");

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (mode === "password") {
      void run(async () => { await api("/auth/login", { method: "POST", json: { email, password } }); done(); });
    } else if (!codeSent) {
      void run(async () => {
        await api("/auth/code/request", { method: "POST", json: { email } });
        setCodeSent(true);
        setInfo(providers?.email_delivery === "log"
          ? "Email isn’t configured on this server yet — the code is printed in the API log."
          : `If ${email} has an account, a 6-digit code is on its way.`);
      });
    } else {
      void run(async () => { await api("/auth/code/verify", { method: "POST", json: { email, code } }); done(); });
    }
  }

  return (
    <div>
      <GoogleButton />
      <div className="mb-5 grid grid-cols-2 gap-1 rounded-xl border border-line bg-surface-2 p-1">
        {(["password", "code"] as Mode[]).map((m) => (
          <button key={m} type="button" onClick={() => { setMode(m); setError(null); setInfo(null); }}
            className={cx("relative rounded-lg py-2 text-[13.5px] font-medium", mode === m ? "text-ink" : "text-ink-3 hover:text-ink-2")}>
            {mode === m && <motion.span layoutId="login-mode" className="absolute inset-0 rounded-lg bg-surface shadow-card" />}
            <span className="relative">{m === "password" ? "Password" : "Email me a code"}</span>
          </button>
        ))}
      </div>
      <form onSubmit={submit} className="space-y-4">
        {error && <ErrorNote message={error} />}
        {info && <p className="rounded-xl bg-brand-soft px-4 py-3 text-[13px] text-brand">{info}</p>}
        <Field label="Work email" type="email" autoComplete="email" required value={email}
          onChange={(e) => { setEmail(e.target.value); setCodeSent(false); }} placeholder="you@business.in" />
        <AnimatePresence initial={false} mode="wait">
          {mode === "password" ? (
            <motion.div key="pw" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
              <Field label="Password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
              <Link href="/forgot" className="mt-2 inline-block text-[12.5px] text-brand hover:underline">Forgot password?</Link>
            </motion.div>
          ) : codeSent ? (
            <motion.div key="code" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
              <Field label="6-digit code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} required value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} className="[&_input]:tracking-[0.5em] [&_input]:font-mono" />
            </motion.div>
          ) : null}
        </AnimatePresence>
        <Button type="submit" size="lg" className="w-full" loading={busy}>
          {mode === "password" ? "Sign in" : codeSent ? "Sign in with code" : "Send me a code"}
        </Button>
      </form>
    </div>
  );
}

export default function LoginPage() {
  return (
    <AuthShell
      title="Welcome back"
      subtitle="Sign in to see today’s decisions."
      footer={<>New to Recova? <Link href="/signup" className="font-medium text-brand hover:underline">Create an account</Link></>}
    >
      <Suspense>
        <LoginForm />
      </Suspense>
    </AuthShell>
  );
}
