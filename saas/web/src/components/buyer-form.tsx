"use client";

import { useState } from "react";
import { Button, Drawer, ErrorNote, Field, Select, Toggle } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import type { BuyerRow } from "@/lib/types";

const EMPTY = {
  name: "", profile: "corporate", language_pref: "english", contact_name: "", contact_email: "",
  contact_phone: "", city: "", state: "", gstin: "", preferred_channel: "email", opted_out: false, sector: "",
};

function initial(buyer?: BuyerRow | null) {
  return buyer ? {
    name: buyer.name, profile: buyer.profile, language_pref: buyer.language_pref,
    contact_name: buyer.contact_name ?? "", contact_email: buyer.contact_email ?? "", contact_phone: buyer.contact_phone ?? "",
    city: buyer.city ?? "", state: buyer.state ?? "", gstin: buyer.gstin ?? "", preferred_channel: buyer.preferred_channel,
    opted_out: buyer.opted_out, sector: buyer.sector ?? "",
  } : EMPTY;
}

export function BuyerForm({ open, onClose, onSaved, buyer }: { open: boolean; onClose: () => void; onSaved: () => void; buyer?: BuyerRow | null }) {
  return (
    <Drawer open={open} onClose={onClose} title={buyer ? "Edit buyer" : "New buyer"} subtitle="A business that owes you money.">
      {/* The drawer mounts its content fresh on every open, so the form starts from the buyer's current values. */}
      <BuyerFormBody buyer={buyer} onClose={onClose} onSaved={onSaved} />
    </Drawer>
  );
}

function BuyerFormBody({ buyer, onClose, onSaved }: { buyer?: BuyerRow | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [f, setF] = useState(() => initial(buyer));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const text = (k: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    const body = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v === "" ? null : v]));
    body.name = f.name;
    try {
      await api(buyer ? `/buyers/${buyer.id}` : "/buyers", { method: buyer ? "PUT" : "POST", json: body });
      toast(buyer ? "Buyer updated" : `${f.name} added`);
      onSaved();
      onClose();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : "Could not save");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      {err && <ErrorNote message={err} />}
      <Field label="Business name" required value={f.name} onChange={text("name")} autoFocus />
      <div className="grid gap-4 sm:grid-cols-2">
        <Select label="Kind of business" value={f.profile} onChange={text("profile")}>
          <option value="corporate">Company / corporate</option>
          <option value="small_trader">Small trader</option>
        </Select>
        <Select label="Message language" value={f.language_pref} onChange={text("language_pref")}>
          <option value="english">English</option>
          <option value="hinglish">Hinglish</option>
        </Select>
        <Field label="Contact person" value={f.contact_name} onChange={text("contact_name")} />
        <Field label="Contact email" type="email" value={f.contact_email} onChange={text("contact_email")} />
        <Field label="Phone / WhatsApp" value={f.contact_phone} onChange={text("contact_phone")} placeholder="+91" />
        <Select label="Preferred channel" value={f.preferred_channel} onChange={text("preferred_channel")}>
          <option value="email">Email</option><option value="whatsapp">WhatsApp</option><option value="sms">SMS</option>
        </Select>
        <Field label="City" value={f.city} onChange={text("city")} />
        <Field label="State" value={f.state} onChange={text("state")} />
        <Field label="GSTIN" value={f.gstin} onChange={text("gstin")} />
        <Field label="Sector" value={f.sector} onChange={text("sector")} />
      </div>
      <p className="text-[12px] text-ink-3">Hinglish is used only for small traders; companies always get English.</p>
      <Toggle checked={f.opted_out} onChange={(v) => setF({ ...f, opted_out: v })} label="This buyer has asked not to be contacted"
        hint="Opt-out stops every reminder at once, whatever else is going on." />
      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
        <Button type="submit" loading={busy}>{buyer ? "Save changes" : "Add buyer"}</Button>
      </div>
    </form>
  );
}
