"use client";

import { motion } from "motion/react";
import { cx } from "@/components/ui";

const RUNGS = [
  { id: 1, name: "Soft nudge" },
  { id: 2, name: "Firm" },
  { id: 3, name: "Legal facts" },
  { id: 4, name: "To a person" },
];

/** Where this invoice sits on the ladder, and how high the law lets it go. */
export function Ladder({ rung, ceiling }: { rung: number; ceiling: number }) {
  return (
    <div>
      <div className="grid grid-cols-4 gap-1.5">
        {RUNGS.map((r, i) => {
          const reached = r.id <= rung;
          const allowed = r.id <= ceiling;
          return (
            <div key={r.id}>
              <div className={cx("relative h-2 overflow-hidden rounded-full", allowed ? "bg-surface-2" : "bg-[repeating-linear-gradient(135deg,var(--line)_0_4px,transparent_4px_8px)]")}>
                {reached && (
                  <motion.div className="absolute inset-0 rounded-full bg-brand" initial={{ scaleX: 0 }} animate={{ scaleX: 1 }}
                    transition={{ delay: 0.15 + i * 0.12, duration: 0.45, ease: [0.16, 1, 0.3, 1] }} style={{ originX: 0 }} />
                )}
              </div>
              <div className={cx("mt-1.5 text-[11.5px]", r.id === rung ? "font-semibold text-ink" : allowed ? "text-ink-3" : "text-ink-3/60")}>{r.id}. {r.name}</div>
            </div>
          );
        })}
      </div>
      <p className="mt-2 text-[12px] text-ink-3">
        The law currently supports up to rung {ceiling}. Striped steps are not yet legally available for this invoice.
      </p>
    </div>
  );
}
