"use client";

import { MotionConfig } from "motion/react";
import { ToastProvider } from "@/components/ui/toast";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    // Every animation in the app honours the OS "reduce motion" setting.
    <MotionConfig reducedMotion="user" transition={{ type: "spring", stiffness: 380, damping: 32 }}>
      <ToastProvider>{children}</ToastProvider>
    </MotionConfig>
  );
}
