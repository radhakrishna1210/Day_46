"use client";

import { motion } from "motion/react";
import { BadgeCheck, Building2, Landmark, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { useSession } from "@/components/session";
import { DigestCard } from "@/components/digest-card";
import { TeamCard } from "@/components/team";
import { Button, Card, CardHeader, ErrorNote, Field, PageHeader, Select, Skeleton } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api, useApi } from "@/lib/api";
import { useLegal, type LegalFigures } from "@/lib/legal";
import type { BusinessProfile } from "@/lib/types";

const FIELDS: { key: keyof BusinessProfile; label: string; hint?: string }[] = [
  { key: "legal_name", label: "Legal name" },
  { key: "udyam_registration", label: "Udyam registration number", hint: "Needed for MSMED Act protection. Format UDYAM-XX-00-0000000." },
  { key: "gstin", label: "GSTIN" }, { key: "pan", label: "PAN" },
  { key: "address_line1", label: "Address line 1" }, { key: "address_line2", label: "Address line 2" },
  { key: "city", label: "City" }, { key: "state", label: "State" }, { key: "pincode", label: "PIN code" },
  { key: "contact_name", label: "Contact person", hint: "Signs the reminders." },
  { key: "contact_email", label: "Contact email" }, { key: "contact_phone", label: "Contact phone" },
];

export default function SettingsPage() {
  const legal = useLegal();
  const profile = useApi<BusinessProfile>("/business");

  if (profile.error) return <ErrorNote message={profile.error} onRetry={profile.reload} />;
  if (!profile.data) return <div className="space-y-4"><Skeleton className="h-10 w-60" /><Skeleton className="h-96" /></div>;
  const p = profile.data;

  return (
    <>
      <PageHeader title="Settings" description="Your business’s legal identity goes on every reminder and every Samadhaan draft." />
      <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
        {/* Remounts whenever the saved profile changes, so the form always starts from what is stored. */}
        <ProfileForm key={JSON.stringify(p)} p={p} onSaved={profile.reload} />
        <SideCards legal={legal} />
      </div>
    </>
  );
}

function ProfileForm({ p, onSaved }: { p: BusinessProfile; onSaved: () => Promise<void> }) {
  const { refresh } = useSession();
  const toast = useToast();
  const [f, setF] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(p).map(([k, v]) => [k, v === null ? "" : String(v)])));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const canEdit = p.role === "owner" || p.role === "admin";

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    const body: Record<string, string | null> = {};
    FIELDS.forEach(({ key }) => { body[key] = f[key] ? f[key] : null; });
    body.enterprise_class = f.enterprise_class || "small";
    try {
      await api("/business", { method: "PUT", json: body });
      toast("Business profile saved");
      await refresh();
      await onSaved();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : "Could not save");
    } finally {
      setBusy(false);
    }
  }

  return (
        <Card>
          <CardHeader title="Business profile" subtitle="Only owners and admins can change this." />
          <form onSubmit={save} className="space-y-5 p-5">
            {err && <ErrorNote message={err} />}
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              className={p.udyam_on_file ? "flex items-center gap-3 rounded-xl bg-[color-mix(in_oklab,var(--good)_10%,transparent)] px-4 py-3 text-[13.5px] text-good-ink" : "flex items-center gap-3 rounded-xl bg-[color-mix(in_oklab,var(--warning)_14%,transparent)] px-4 py-3 text-[13.5px] text-warning-ink"}>
              {p.udyam_on_file ? <BadgeCheck className="size-5 shrink-0" /> : <ShieldAlert className="size-5 shrink-0" />}
              {p.udyam_on_file
                ? "Udyam registration on file. Samadhaan drafts can be marked ready to file."
                : "No Udyam registration yet. Statutory interest only protects registered micro and small enterprises, and Samadhaan drafts stay blocked until you add it."}
            </motion.div>
            <div className="grid gap-4 sm:grid-cols-2">
              {FIELDS.map(({ key, label, hint }) => (
                <Field key={key} label={label} hint={hint} disabled={!canEdit} value={f[key] ?? ""} onChange={(e) => setF({ ...f, [key]: e.target.value })}
                  className={key === "legal_name" || key === "udyam_registration" ? "sm:col-span-2" : ""} required={key === "legal_name"} />
              ))}
              <Select label="Enterprise class" disabled={!canEdit} value={f.enterprise_class ?? "small"} onChange={(e) => setF({ ...f, enterprise_class: e.target.value })}>
                <option value="micro">Micro</option><option value="small">Small</option><option value="medium">Medium</option>
              </Select>
            </div>
            {canEdit && <div className="flex justify-end"><Button type="submit" loading={busy}>Save profile</Button></div>}
          </form>
        </Card>
  );
}

function SideCards({ legal }: { legal: LegalFigures | null }) {
  return (
        <div className="space-y-6">
          <TeamCard />
          <DigestCard />

          <Card>
            <CardHeader title="Legal figures in use" subtitle={legal ? `As of ${legal.as_of}` : "Loading…"} action={<Landmark className="size-4 text-ink-3" />} />
            {legal && (
              <dl className="mt-3 space-y-2 px-5 pb-5 text-[13px]">
                <div className="flex justify-between"><dt className="text-ink-2">Term with no written agreement</dt><dd className="font-medium">{legal.no_agreement_days} days</dd></div>
                <div className="flex justify-between"><dt className="text-ink-2">Longest agreed term allowed</dt><dd className="font-medium">{legal.max_agreement_days} days</dd></div>
                <div className="flex justify-between"><dt className="text-ink-2">Interest rate</dt><dd className="font-medium">{legal.bank_rate_multiplier}× bank rate · {legal.effective_annual_rate_pct}% p.a.</dd></div>
                <p className="pt-2 text-[12px] leading-relaxed text-ink-3">{legal.disclaimer}</p>
              </dl>
            )}
          </Card>

          <Card className="p-5">
            <div className="flex items-center gap-2 text-[13.5px] font-semibold"><Building2 className="size-4" /> Channels</div>
            <p className="mt-1.5 text-[13px] text-ink-2">Email, WhatsApp and payment links aren’t connected yet. Recova drafts every message; you send it and mark it sent.</p>
          </Card>
        </div>
  );
}
