"use client";

import { AnimatePresence, motion } from "motion/react";
import { CheckCircle2, CircleAlert } from "lucide-react";
import { createContext, useCallback, useContext, useState } from "react";

type Toast = { id: number; tone: "good" | "bad"; text: string };
const ToastContext = createContext<(text: string, tone?: Toast["tone"]) => void>(() => {});

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: Toast["tone"] = "good") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, tone, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed bottom-5 right-5 z-[100] flex w-[min(380px,calc(100vw-2.5rem))] flex-col gap-2">
        <AnimatePresence initial={false}>
          {toasts.map((t) => (
            <motion.div
              key={t.id}
              layout
              initial={{ opacity: 0, y: 16, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, x: 40, transition: { duration: 0.18 } }}
              className="pointer-events-auto flex items-start gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-sm shadow-float"
            >
              {t.tone === "good" ? (
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-good-ink" aria-hidden />
              ) : (
                <CircleAlert className="mt-0.5 size-4 shrink-0 text-critical-ink" aria-hidden />
              )}
              <span className="text-ink">{t.text}</span>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}
