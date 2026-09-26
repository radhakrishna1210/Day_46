"use client";

import Link from "next/link";
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
    // Also reached from "Create a business" in the switcher, so an account that
    // already has one may be here on purpose.
    api<Session>("/auth/session").then(setSession, () => router.replace("/login"));
  }, [router]);
  const first = session && !session.businesses.length;

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
    <AuthShell title={!session ? "Welcome" : first ? `Welcome, ${session.user.name.split(" ")[0]}` : "Add a business"}
      subtitle={first || !session
        ? "One last thing: what’s your business called? It goes on every reminder you send."
        : "A separate workspace with its own buyers, invoices and audit trail. Switch between them from the sidebar."}
      footer={<span className="text-ink-3">You can change it, and add more businesses, later.</span>}>
      <form onSubmit={submit} className="space-y-4">
        {error && <ErrorNote message={error} />}
        <Field label="Business name" required minLength={2} value={name} onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Shree Ganesh Fabricators" autoFocus />
        <Button type="submit" size="lg" className="w-full" loading={busy} disabled={!session}>
          {first ? "Create my workspace" : "Create business"}
        </Button>
        {session && (session.user.is_super_admin || !first) && (
          <Link href={session.active_business ? "/app" : "/app/platform"} className="block text-center text-[13.5px] text-brand hover:underline">
            {session.active_business ? "Back to the app" : "Skip — go to the Platform"}
          </Link>
        )}
      </form>
    </AuthShell>
  );
}
