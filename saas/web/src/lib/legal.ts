"use client";

import { useApi } from "@/lib/api";

/** Statutory figures, served from config/legal.yaml by the API. The UI never
 *  types a legal number itself. */
export type LegalFigures = {
  no_agreement_days: number;
  max_agreement_days: number;
  bank_rate_multiplier: number;
  effective_annual_rate_pct: number;
  as_of: string;
  disclaimer: string;
};

export function useLegal() {
  return useApi<LegalFigures>("/meta/legal").data;
}
