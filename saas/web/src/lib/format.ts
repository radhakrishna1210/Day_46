/** Money arrives from the API in integer paise; it is formatted only here. */

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const inrExact = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2 });

export function rupees(paise: number | null | undefined, exact = false): string {
  if (paise === null || paise === undefined) return "—";
  return (exact ? inrExact : inr).format(paise / 100);
}

/** ₹12.4L / ₹3.1Cr -- for tiles and axes where the full figure would crowd. */
export function rupeesShort(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return "—";
  const r = paise / 100;
  const abs = Math.abs(r);
  if (abs >= 1e7) return `₹${(r / 1e7).toFixed(abs >= 1e8 ? 0 : 2)}Cr`;
  if (abs >= 1e5) return `₹${(r / 1e5).toFixed(abs >= 1e6 ? 1 : 2)}L`;
  if (abs >= 1e3) return `₹${(r / 1e3).toFixed(1)}K`;
  return `₹${Math.round(r)}`;
}

export function date(iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", year: "numeric" }): string {
  if (!iso) return "—";
  return new Date(iso.length === 10 ? `${iso}T00:00:00` : iso).toLocaleDateString("en-IN", opts);
}

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString("en-IN")} ${n === 1 ? one : many}`;
}

export function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("");
}

export function toPaise(rupeesText: string): number | null {
  const n = Number(rupeesText.replace(/[,\s₹]/g, ""));
  return Number.isFinite(n) && n > 0 ? Math.round(n * 100) : null;
}
