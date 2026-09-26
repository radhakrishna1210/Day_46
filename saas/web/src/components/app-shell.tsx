"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import {
  Building2, Check, ChevronsUpDown, Eye, FileText, Gavel, LayoutDashboard, LogOut, MailCheck, Menu, Moon,
  ScrollText, Settings, ShieldAlert, ShieldCheck, Sun, Upload, Users, X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Logo } from "@/components/logo";
import { SessionContext, useSession } from "@/components/session";
import { cx, Skeleton } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { initials } from "@/lib/format";
import type { Session } from "@/lib/types";

const NAV = [
  { href: "/app", label: "Overview", icon: LayoutDashboard },
  { href: "/app/decisions", label: "Today’s decisions", icon: Gavel },
  { href: "/app/invoices", label: "Invoices", icon: FileText },
  { href: "/app/buyers", label: "Buyers", icon: Users },
  { href: "/app/import", label: "Import", icon: Upload },
  { href: "/app/audit", label: "Audit trail", icon: ScrollText },
  { href: "/app/settings", label: "Settings", icon: Settings },
];

// Only for platform super admins (the API decides; this just hides the link).
const PLATFORM_NAV = { href: "/app/platform", label: "Platform", icon: ShieldCheck };

/** The theme lives on <html data-theme> (or the OS setting) -- external state,
 *  so it is read with useSyncExternalStore rather than mirrored into React. */
function subscribeTheme(onChange: () => void) {
  const mo = new MutationObserver(onChange);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  mq.addEventListener("change", onChange);
  return () => { mo.disconnect(); mq.removeEventListener("change", onChange); };
}
function readTheme(): "light" | "dark" {
  const stamped = document.documentElement.dataset.theme;
  if (stamped === "light" || stamped === "dark") return stamped;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function useTheme() {
  const theme = useSyncExternalStore(subscribeTheme, readTheme, () => "light" as const);
  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("recova-theme", next); } catch {}
  };
  return { theme, toggle };
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  // The mobile menu is open FOR a page; navigating anywhere closes it.
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const pathname = usePathname();
  const mobileOpen = menuFor === pathname;
  const setMobileOpen = (open: boolean) => setMenuFor(open ? pathname : null);

  const refresh = useCallback(async () => {
    setSession(await api<Session>("/auth/session"));
  }, []);
  const router = useRouter();
  useEffect(() => {
    let live = true;
    api<Session>("/auth/session").then((s) => {
      if (!live) return;
      // Signed in but no business yet (a new Google sign-up): name one first --
      // except a platform super admin, who needs no business of their own.
      if (!s.active_business && !s.user.is_super_admin) router.replace("/welcome");
      else setSession(s);
    }, () => {});
    return () => { live = false; };
  }, [router]);

  // A super admin without a business has only the Platform page to show.
  const platformOnly = !!session && !session.active_business;
  useEffect(() => {
    if (platformOnly && pathname !== PLATFORM_NAV.href) router.replace(PLATFORM_NAV.href);
  }, [platformOnly, pathname, router]);

  if (!session || (platformOnly && pathname !== PLATFORM_NAV.href)) {
    return (
      <div className="flex min-h-screen">
        <div className="hidden w-64 border-r border-line p-4 lg:block"><Skeleton className="h-8 w-32" /></div>
        <div className="flex-1 p-10"><Skeleton className="h-10 w-72" /><Skeleton className="mt-6 h-40 w-full" /></div>
      </div>
    );
  }

  return (
    <SessionContext.Provider value={{ session, refresh }}>
      <div className="flex min-h-screen">
        <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r border-line bg-bg-raised lg:flex">
          <Sidebar session={session} />
        </aside>

        <AnimatePresence>
          {mobileOpen && (
            <div className="fixed inset-0 z-50 lg:hidden">
              <motion.div className="absolute inset-0 bg-black/40" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setMobileOpen(false)} />
              <motion.aside
                className="absolute inset-y-0 left-0 flex w-72 flex-col border-r border-line bg-bg-raised"
                initial={{ x: "-100%" }} animate={{ x: 0 }} exit={{ x: "-100%" }}
              >
                <button className="absolute right-3 top-4 rounded-lg p-1.5 text-ink-3" onClick={() => setMobileOpen(false)} aria-label="Close menu"><X className="size-5" /></button>
                <Sidebar session={session} />
              </motion.aside>
            </div>
          )}
        </AnimatePresence>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="sticky top-0 z-30 flex items-center gap-3 border-b border-line bg-bg/80 px-4 py-3 backdrop-blur-lg lg:hidden">
            <button onClick={() => setMobileOpen(true)} className="rounded-lg p-1.5 text-ink-2" aria-label="Open menu"><Menu className="size-5" /></button>
            <Logo />
          </div>
          {!session.user.email_verified && <VerifyEmailBanner email={session.user.email} onVerified={refresh} />}
          {session.active_business?.suspended && (
            <div className="flex items-center gap-2 border-b border-line bg-[color-mix(in_oklab,var(--critical)_12%,var(--bg))] px-4 py-2.5 text-[13px] text-critical-ink sm:px-8">
              <ShieldAlert className="size-4 shrink-0" aria-hidden />
              {session.active_business.name} is suspended by Recova, so its data can’t be opened. Contact Recova support.
            </div>
          )}
          {session.active_business?.role === "viewer" && (
            <div className="flex items-center gap-2 border-b border-line bg-surface-2 px-4 py-2 text-[13px] text-ink-2 sm:px-8">
              <Eye className="size-4 shrink-0 text-ink-3" aria-hidden />
              View only — you can see everything in {session.active_business.name}, but not change it.
            </div>
          )}
          <main className="mx-auto w-full max-w-[1240px] flex-1 px-4 py-8 sm:px-8 lg:py-10">
            <AnimatePresence mode="wait">
              <motion.div
                // Keyed by business too: switching remounts every page, so
                // nothing from the previous business can linger on screen.
                key={`${session.active_business?.id}:${pathname}`}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
              >
                {children}
              </motion.div>
            </AnimatePresence>
          </main>
        </div>
      </div>
    </SessionContext.Provider>
  );
}

function VerifyEmailBanner({ email, onVerified }: { email: string; onVerified: () => Promise<void> }) {
  const toast = useToast();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);

  async function resend() {
    try {
      const r = await api<{ message: string }>("/auth/verify-email/send", { method: "POST" });
      toast(r.message);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not send the code", "bad");
    }
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/auth/verify-email", { method: "POST", json: { code } });
      toast("Email confirmed");
      await onVerified();
    } catch (err) {
      toast(err instanceof Error ? err.message : "That code didn’t work", "bad");
    } finally {
      setBusy(false);
    }
  }

  return (
    <motion.form onSubmit={verify} initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }}
      className="flex flex-wrap items-center gap-3 border-b border-line bg-[color-mix(in_oklab,var(--warning)_12%,var(--bg))] px-4 py-2.5 text-[13px] sm:px-8">
      <MailCheck className="size-4 shrink-0 text-warning-ink" aria-hidden />
      <span className="text-ink-2">Confirm <b className="font-medium text-ink">{email}</b> — enter the 6-digit code we emailed you.</span>
      <input value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} inputMode="numeric"
        autoComplete="one-time-code" aria-label="Verification code" placeholder="000000"
        className="h-8 w-28 rounded-lg border border-line-strong bg-surface px-2 text-center font-mono tracking-[0.3em] outline-none focus:border-brand" />
      <button type="submit" disabled={busy || code.length !== 6} className="h-8 rounded-lg bg-brand px-3 font-medium text-brand-ink disabled:opacity-50">Confirm</button>
      <button type="button" onClick={() => void resend()} className="text-brand hover:underline">Send a new code</button>
    </motion.form>
  );
}

function Sidebar({ session }: { session: Session }) {
  const pathname = usePathname();
  const active = (href: string) => (href === "/app" ? pathname === "/app" : pathname.startsWith(href));
  const role = session.active_business?.role;
  const base = role === "viewer" ? NAV.filter((n) => n.href !== "/app/import") : NAV;
  const items = !session.active_business ? [PLATFORM_NAV]
    : session.user.is_super_admin ? [...base, PLATFORM_NAV] : base;
  return (
    <>
      <div className="px-5 pt-5 pb-4"><Link href={session.active_business ? "/app" : PLATFORM_NAV.href}><Logo /></Link></div>
      <div className="px-3"><BusinessSwitcher session={session} /></div>
      <nav className="mt-4 flex-1 space-y-0.5 px-3">
        {items.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={cx("relative flex items-center gap-3 rounded-xl px-3 py-2 text-[14px] transition-colors",
              active(href) ? "text-ink" : "text-ink-2 hover:text-ink")}
          >
            {active(href) && (
              <motion.span layoutId="nav-active" className="absolute inset-0 rounded-xl bg-surface shadow-card ring-1 ring-line"
                transition={{ type: "spring", stiffness: 420, damping: 34 }} />
            )}
            <Icon className={cx("relative size-[18px]", active(href) && "text-brand")} aria-hidden />
            <span className="relative">{label}</span>
          </Link>
        ))}
      </nav>
      <UserMenu session={session} />
    </>
  );
}

function BusinessSwitcher({ session }: { session: Session }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const toast = useToast();
  const router = useRouter();
  const { refresh } = useSession();
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const current = session.active_business;

  async function switchTo(id: string) {
    if (id === current?.id) return setOpen(false);
    await api("/auth/switch", { method: "POST", json: { tenant_id: id } });
    setOpen(false);
    await refresh();
    router.push("/app");
    toast("Switched business");
  }

  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-3 rounded-xl border border-line bg-surface px-3 py-2.5 text-left shadow-card hover:border-line-strong">
        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-brand-soft text-[12px] font-semibold text-brand">{initials(current?.name ?? "Recova platform")}</span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13.5px] font-medium">{current?.name ?? "Recova platform"}</span>
          <span className="block text-[11.5px] text-ink-3 capitalize">{current ? current.role : "Super admin"}</span>
        </span>
        <ChevronsUpDown className="size-4 text-ink-3" />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -6, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -6, scale: 0.98 }}
            className="absolute inset-x-0 top-full z-50 mt-2 overflow-hidden rounded-xl border border-line bg-surface p-1 shadow-float"
          >
            <p className="px-3 pt-2 pb-1 text-[11px] font-medium tracking-wide text-ink-3 uppercase">Your businesses</p>
            {session.businesses.map((b) => (
              <button key={b.id} onClick={() => void switchTo(b.id)} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[13.5px] hover:bg-surface-2">
                <Building2 className="size-4 text-ink-3" />
                <span className="flex-1 truncate">{b.name}</span>
                {b.id === current?.id && <Check className="size-4 text-brand" />}
              </button>
            ))}
            {!session.businesses.length && <p className="px-3 pb-1 text-[13px] text-ink-3">None yet.</p>}
            <Link href="/welcome" onClick={() => setOpen(false)} className="mt-1 flex items-center gap-2 rounded-lg border-t border-line px-3 py-2 text-[13.5px] text-brand hover:bg-surface-2">
              <Building2 className="size-4" /> Create a business
            </Link>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function UserMenu({ session }: { session: Session }) {
  const router = useRouter();
  const { theme, toggle } = useTheme();
  async function signOut() {
    await api("/auth/logout", { method: "POST" });
    router.push("/login");
  }
  return (
    <div className="border-t border-line p-3">
      <div className="flex items-center gap-3 px-2 py-1.5">
        <span className="grid size-8 shrink-0 place-items-center rounded-full bg-ink text-[12px] font-semibold text-bg">{initials(session.user.name)}</span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13.5px] font-medium">{session.user.name}</span>
          <span className="block truncate text-[11.5px] text-ink-3">{session.user.email}</span>
        </span>
      </div>
      <div className="mt-1 flex gap-1">
        <button onClick={toggle} className="flex flex-1 items-center justify-center gap-2 rounded-lg px-2 py-1.5 text-[12.5px] text-ink-2 hover:bg-surface-2 hover:text-ink" aria-label="Toggle theme">
          <motion.span key={theme} initial={{ rotate: -90, opacity: 0 }} animate={{ rotate: 0, opacity: 1 }}>
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </motion.span>
          {theme === "dark" ? "Light" : "Dark"}
        </button>
        <button onClick={() => void signOut()} className="flex flex-1 items-center justify-center gap-2 rounded-lg px-2 py-1.5 text-[12.5px] text-ink-2 hover:bg-surface-2 hover:text-ink">
          <LogOut className="size-4" /> Sign out
        </button>
      </div>
    </div>
  );
}
