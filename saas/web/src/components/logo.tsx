import { useId } from "react";
import { cx } from "@/components/ui";

/** The Recova mark: a returning arc -- money coming back round. The gradient
 *  id is unique per instance: with a shared id, a copy inside a hidden element
 *  (the desktop sidebar on a phone) left every other copy unpainted. */
export function Logo({ className, wordmark = true }: { className?: string; wordmark?: boolean }) {
  const gradient = `recova-g-${useId().replace(/:/g, "")}`;
  return (
    <span className={cx("inline-flex items-center gap-2", className)}>
      <svg viewBox="0 0 32 32" className="size-7" aria-hidden>
        <defs>
          <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="var(--brand-glow)" />
            <stop offset="1" stopColor="var(--brand)" />
          </linearGradient>
        </defs>
        <rect width="32" height="32" rx="9" fill="var(--brand)" />
        <rect width="32" height="32" rx="9" fill={`url(#${gradient})`} />
        <path d="M10 21.5V11h6.2a4.3 4.3 0 0 1 0 8.6H13.5l5.8 5.4" fill="none" stroke="white" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {wordmark && <span className="text-[17px] font-semibold tracking-tight">Recova</span>}
    </span>
  );
}
