"use client";

import clsx from "clsx";
import { AnimatePresence, animate, motion, useInView, useMotionValue, useTransform } from "motion/react";
import { AlertTriangle, CheckCircle2, CircleDot, Clock, Loader2, OctagonAlert, X } from "lucide-react";
import { forwardRef, useEffect, useId, useRef } from "react";

export const cx = clsx;

/* ---------------------------------------------------------------- button */

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
  icon?: React.ReactNode;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", loading, icon, className, children, disabled, ...rest }, ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cx(
        "group relative inline-flex items-center justify-center gap-2 rounded-xl font-medium whitespace-nowrap",
        "transition-[background,box-shadow,transform,color] duration-150 active:scale-[0.97] disabled:pointer-events-none disabled:opacity-50",
        size === "sm" && "h-8 px-3 text-[13px]",
        size === "md" && "h-10 px-4 text-sm",
        size === "lg" && "h-12 px-6 text-[15px]",
        variant === "primary" && "bg-brand text-brand-ink shadow-[0_1px_0_rgb(255_255_255/0.15)_inset,0_8px_20px_-10px_var(--brand)] hover:brightness-110",
        variant === "secondary" && "border border-line-strong bg-surface text-ink hover:bg-surface-2",
        variant === "ghost" && "text-ink-2 hover:bg-surface-2 hover:text-ink",
        variant === "danger" && "bg-critical text-white hover:brightness-110",
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
});

/* ------------------------------------------------------------------ card */

export function Card({ className, children, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cx("rounded-2xl border border-line bg-surface shadow-card", className)} {...rest}>
      {children}
    </div>
  );
}

export function CardHeader({ title, subtitle, action }: { title: React.ReactNode; subtitle?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 px-5 pt-5">
      <div className="min-w-0">
        <h2 className="text-[15px] font-semibold tracking-tight text-ink">{title}</h2>
        {subtitle && <p className="mt-0.5 text-[13px] text-ink-3">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

/* ----------------------------------------------------------------- forms */

type FieldProps = React.InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string; error?: string | null };

export const Field = forwardRef<HTMLInputElement, FieldProps>(function Field({ label, hint, error, className, id, ...rest }, ref) {
  const auto = useId();
  const inputId = id ?? auto;
  return (
    <label htmlFor={inputId} className={cx("block", className)}>
      <span className="mb-1.5 block text-[13px] font-medium text-ink-2">{label}</span>
      <input
        ref={ref}
        id={inputId}
        aria-invalid={!!error || undefined}
        className={cx(
          "h-11 w-full rounded-xl border bg-surface px-3.5 text-[15px] text-ink placeholder:text-ink-3",
          "transition-[border-color,box-shadow] outline-none focus:border-brand focus:shadow-[0_0_0_4px_color-mix(in_oklab,var(--brand)_18%,transparent)]",
          error ? "border-critical" : "border-line-strong",
        )}
        {...rest}
      />
      {(error || hint) && (
        <span className={cx("mt-1.5 block text-[12px]", error ? "text-critical-ink" : "text-ink-3")}>{error ?? hint}</span>
      )}
    </label>
  );
});

export function Select({ label, className, children, ...rest }: React.SelectHTMLAttributes<HTMLSelectElement> & { label: string }) {
  const id = useId();
  return (
    <label htmlFor={id} className={cx("block", className)}>
      <span className="mb-1.5 block text-[13px] font-medium text-ink-2">{label}</span>
      <select
        id={id}
        className="h-11 w-full rounded-xl border border-line-strong bg-surface px-3 text-[15px] text-ink outline-none focus:border-brand"
        {...rest}
      >
        {children}
      </select>
    </label>
  );
}

export function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="flex w-full items-start gap-3 rounded-xl text-left"
    >
      <span className={cx("relative mt-0.5 h-6 w-10 shrink-0 rounded-full transition-colors", checked ? "bg-brand" : "bg-line-strong")}>
        <motion.span layout className={cx("absolute top-1 size-4 rounded-full bg-white shadow", checked ? "right-1" : "left-1")} />
      </span>
      <span>
        <span className="block text-sm font-medium text-ink">{label}</span>
        {hint && <span className="block text-[12px] text-ink-3">{hint}</span>}
      </span>
    </button>
  );
}

/* ---------------------------------------------------------------- status */

const STATUS = {
  good: { icon: CheckCircle2, cls: "bg-[color-mix(in_oklab,var(--good)_14%,transparent)] text-good-ink" },
  warning: { icon: Clock, cls: "bg-[color-mix(in_oklab,var(--warning)_18%,transparent)] text-warning-ink" },
  serious: { icon: AlertTriangle, cls: "bg-[color-mix(in_oklab,var(--serious)_18%,transparent)] text-serious-ink" },
  critical: { icon: OctagonAlert, cls: "bg-[color-mix(in_oklab,var(--critical)_14%,transparent)] text-critical-ink" },
  neutral: { icon: CircleDot, cls: "bg-surface-2 text-ink-2" },
  brand: { icon: CircleDot, cls: "bg-brand-soft text-brand" },
} as const;

/** Status never rides on colour alone: every pill carries an icon and a word. */
export function Pill({ tone = "neutral", children, icon = true }: { tone?: keyof typeof STATUS; children: React.ReactNode; icon?: boolean }) {
  const { icon: Icon, cls } = STATUS[tone];
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[12px] font-medium whitespace-nowrap", cls)}>
      {icon && <Icon className="size-3" aria-hidden />}
      {children}
    </span>
  );
}

/* -------------------------------------------------------------- skeleton */

export function Skeleton({ className }: { className?: string }) {
  return (
    <div className={cx("relative overflow-hidden rounded-lg bg-surface-2", className)}>
      <motion.div
        className="absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-[color-mix(in_oklab,var(--ink)_6%,transparent)] to-transparent"
        animate={{ x: ["-100%", "100%"] }}
        transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
      />
    </div>
  );
}

/* ------------------------------------------------------- animated number */

export function AnimatedNumber({ value, format }: { value: number; format: (n: number) => string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true });
  const mv = useMotionValue(0);
  const text = useTransform(mv, (v) => format(v));
  useEffect(() => {
    if (!inView) return;
    const controls = animate(mv, value, { duration: 1.1, ease: [0.16, 1, 0.3, 1] });
    return () => controls.stop();
  }, [inView, value, mv]);
  useEffect(() => text.on("change", (v) => { if (ref.current) ref.current.textContent = v; }), [text]);
  return <span ref={ref} className="tnum">{format(0)}</span>;
}

/* ----------------------------------------------------------------- drawer */

export function Drawer({ open, onClose, title, subtitle, children, footer }: {
  open: boolean; onClose: () => void; title: React.ReactNode; subtitle?: React.ReactNode;
  children: React.ReactNode; footer?: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50">
          <motion.div
            className="absolute inset-0 bg-[rgb(8_10_14/0.45)] backdrop-blur-[2px]"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.aside
            role="dialog" aria-modal="true"
            className="absolute right-0 top-0 flex h-full w-full max-w-xl flex-col border-l border-line bg-bg-raised shadow-float"
            initial={{ x: "100%" }} animate={{ x: 0 }} exit={{ x: "100%" }}
            transition={{ type: "spring", stiffness: 320, damping: 34 }}
          >
            <div className="flex items-start justify-between gap-4 border-b border-line px-6 py-5">
              <div className="min-w-0">
                <h2 className="truncate text-lg font-semibold tracking-tight">{title}</h2>
                {subtitle && <div className="mt-0.5 text-[13px] text-ink-3">{subtitle}</div>}
              </div>
              <button onClick={onClose} className="rounded-lg p-1.5 text-ink-3 hover:bg-surface-2 hover:text-ink" aria-label="Close">
                <X className="size-5" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>
            {footer && <div className="border-t border-line px-6 py-4">{footer}</div>}
          </motion.aside>
        </div>
      )}
    </AnimatePresence>
  );
}

/* ------------------------------------------------------------ page chrome */

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="mb-7 flex flex-wrap items-end justify-between gap-4"
    >
      <div className="min-w-0">
        {eyebrow && <div className="mb-1 text-[12px] font-medium tracking-[0.14em] text-brand uppercase">{eyebrow}</div>}
        <h1 className="font-display text-[34px] leading-[1.05] tracking-tight text-ink sm:text-[40px]">{title}</h1>
        {description && <p className="mt-2 max-w-2xl text-[15px] text-ink-2">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </motion.div>
  );
}

export const stagger = {
  container: { hidden: {}, show: { transition: { staggerChildren: 0.06, delayChildren: 0.05 } } },
  item: { hidden: { opacity: 0, y: 14 }, show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: [0.16, 1, 0.3, 1] as const } } },
};

export function EmptyState({ icon, title, body, action }: { icon: React.ReactNode; title: string; body: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
      <motion.div
        initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
        className="mb-4 grid size-14 place-items-center rounded-2xl bg-brand-soft text-brand"
      >
        {icon}
      </motion.div>
      <h3 className="text-lg font-semibold tracking-tight">{title}</h3>
      <p className="mt-1 max-w-sm text-sm text-ink-2">{body}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl border border-[color-mix(in_oklab,var(--critical)_40%,transparent)] bg-[color-mix(in_oklab,var(--critical)_8%,transparent)] px-4 py-3 text-sm text-critical-ink">
      <span className="flex items-center gap-2"><OctagonAlert className="size-4" aria-hidden /> {message}</span>
      {onRetry && <Button size="sm" variant="secondary" onClick={onRetry}>Retry</Button>}
    </div>
  );
}
