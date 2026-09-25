"use client";

import { motion } from "motion/react";

/** A buyer's 0-100 payment score. The number is always printed -- the ring's
 *  colour is reinforcement, never the only signal. */
export function ScoreRing({ score, size = 52 }: { score: number; size?: number }) {
  const r = (size - 6) / 2;
  const c = 2 * Math.PI * r;
  const tone = score >= 80 ? "var(--good)" : score >= 50 ? "var(--warning)" : "var(--critical)";
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} aria-label={`Payment score ${score} out of 100`}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-2)" strokeWidth={5} />
        <motion.circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={tone} strokeWidth={5} strokeLinecap="round"
          strokeDasharray={c} initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c * (1 - score / 100) }}
          transition={{ duration: 1.1, ease: [0.16, 1, 0.3, 1] }} />
      </svg>
      <span className="tnum absolute inset-0 grid place-items-center text-[13px] font-semibold">{score}</span>
    </div>
  );
}
