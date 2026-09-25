"use client";

import Link from "next/link";
import { motion } from "motion/react";
import { Logo } from "@/components/logo";

const QUOTES = [
  "The statutory due date, not the one on the purchase order.",
  "Interest that compounds whether or not anyone mentions it.",
  "A dispute goes to a person. Always.",
];

export function AuthShell({ title, subtitle, children, footer }: { title: string; subtitle: string; children: React.ReactNode; footer: React.ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-[1fr_1.05fr]">
      <div className="flex flex-col px-6 py-8 sm:px-12">
        <Link href="/" className="w-fit"><Logo /></Link>
        <div className="flex flex-1 items-center">
          <motion.div
            initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
            className="mx-auto w-full max-w-sm py-12"
          >
            <h1 className="font-display text-[42px] leading-none tracking-tight">{title}</h1>
            <p className="mt-3 text-[15px] text-ink-2">{subtitle}</p>
            <div className="mt-8">{children}</div>
            <div className="mt-6 text-[14px] text-ink-2">{footer}</div>
          </motion.div>
        </div>
      </div>

      <div className="grain relative hidden overflow-hidden bg-[#07090c] lg:block">
        <motion.div
          aria-hidden
          className="absolute -right-24 -top-24 size-[560px] rounded-full bg-[radial-gradient(circle,#0f766e_0%,transparent_65%)] blur-3xl"
          animate={{ scale: [1, 1.15, 1], x: [0, -40, 0] }}
          transition={{ duration: 18, repeat: Infinity, ease: "easeInOut" }}
        />
        <motion.div
          aria-hidden
          className="absolute -bottom-40 -left-20 size-[520px] rounded-full bg-[radial-gradient(circle,#2a78d6_0%,transparent_65%)] opacity-60 blur-3xl"
          animate={{ scale: [1.1, 1, 1.1], y: [0, -30, 0] }}
          transition={{ duration: 22, repeat: Infinity, ease: "easeInOut" }}
        />
        <div className="relative flex h-full flex-col justify-end p-14 text-[#f2f1ec]">
          <div className="space-y-4">
            {QUOTES.map((q, i) => (
              <motion.p
                key={q}
                initial={{ opacity: 0, x: 24 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.3 + i * 0.18, duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
                className="max-w-md font-display text-[30px] leading-tight tracking-tight text-white/90"
              >
                {q}
              </motion.p>
            ))}
          </div>
          <p className="mt-10 text-[13px] text-white/45">Recova · receivables recovery for Indian MSMEs</p>
        </div>
      </div>
    </div>
  );
}
