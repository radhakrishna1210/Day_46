"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthShell } from "@/components/auth-shell";
import { Button, ErrorNote, Field } from "@/components/ui";
import { api } from "@/lib/api";
import type { Session } from "@/lib/types";

/** First stop for someone who signed up with Google: name the business. */
export default function WelcomePage() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Session>("/auth/session").then(
      (s) => { if (s.active_business) router.replace("/app"); else setSession(s); },
      () => router.replace("/login"),
    );
  }, [router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/business/create", { method: "POST", json: { legal_name: name } });
      router.push("/app?welcome=1");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the business");
      setBusy(false);
    }
  }

  return (
    <AuthShell title={session ? `Welcome, ${session.user.name.split(" ")[0]}` : "Welcome"}
      subtitle="One last thing: what’s your business called? It goes on every reminder you send."
      footer={<span className="text-ink-3">You can change it, and add more businesses, later.</span>}>
      <form onSubmit={submit} className="space-y-4">
        {error && <ErrorNote message={error} />}
        <Field label="Business name" required minLength={2} value={name} onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Shree Ganesh Fabricators" autoFocus />
        <Button type="submit" size="lg" className="w-full" loading={busy} disabled={!session}>Create my workspace</Button>
      </form>
    </AuthShell>
  );
}
