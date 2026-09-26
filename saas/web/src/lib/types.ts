export type Business = { id: string; name: string; role: Role; suspended?: boolean };

export type Role = "owner" | "admin" | "member" | "viewer";

export type Team = {
  my_role: Role;
  can_manage: boolean;
  members: { id: string; user_id: string; name: string; email: string; role: Role; is_me: boolean }[];
  invites: { id: string; email: string; role: Role; invited_by: string; created_at: string; expires_at: string; status: "pending" | "expired" }[];
};

export type InviteInfo = {
  business: string; invited_by: string; role: Role; email: string;
  status: "pending" | "accepted" | "revoked" | "expired"; has_account: boolean;
};

export type Session = {
  user: { id: string; name: string; email: string; email_verified: boolean; has_password: boolean; google_linked: boolean; is_super_admin: boolean; digest_opt_out: boolean };
  businesses: Business[];
  active_business: Business | null;
};

export type Score = {
  value: number;
  confidence: "low" | "medium" | "high";
  history_count?: number;
  trend?: string | null;
  breakdown: { factor: string; detail: string; points: number }[];
};

export type BuyerRow = {
  id: string; code: string; name: string; profile: "corporate" | "small_trader";
  sector: string | null; language_pref: "english" | "hinglish";
  contact_name: string | null; contact_email: string | null; contact_phone: string | null;
  city: string | null; state: string | null; gstin: string | null;
  preferred_channel: "email" | "whatsapp" | "sms"; opted_out: boolean;
  score: Score | null;
  invoice_count?: number; outstanding_paise?: number; overdue_count?: number; overdue_paise?: number;
  invoices?: { id: string; invoice_number: string; amount_paise: number; outstanding_paise: number; days_overdue: number }[];
};

export type InvoiceStatus = "open" | "overdue" | "paid" | "disputed";

export type InvoiceRow = {
  id: string; invoice_number: string;
  buyer: { id: string; name: string; code: string };
  description: string | null; po_number: string | null;
  amount_paise: number; outstanding_paise: number; paid_paise: number;
  issue_date: string; acceptance_date: string;
  written_agreement: boolean; agreed_days: number | null;
  disputed: boolean; status: InvoiceStatus;
  statutory_due_date: string; days_overdue: number; interest_paise: number;
  /** Days until the statutory due date; negative once overdue. */
  days_to_due: number;
};

export type Legal = {
  statutory_due_date: string; interest_from: string; days_overdue: number;
  principal_paise: number; interest_paise: number; total_payable_paise: number;
  interest_per_day_paise: number; cost_of_waiting_paise: number; waiting_horizon_days: number;
  buyer_tax_exposure_paise: number; tax_deduction_crystallised: boolean;
  available_rung: number; agreed_term_void: boolean; days_gained_by_law: number;
  dispute_hold: boolean; facts: string[];
};

export type InvoiceDetail = InvoiceRow & {
  dispute_note: string | null;
  legal: Legal;
  payments: { id: string; paid_on: string; amount_paise: number; note: string | null }[];
  promises: { id: string; promised_date: string; amount: "full" | "partial"; status: "open" | "kept" | "broken"; recorded_on: string; note: string | null }[];
  contacts: { id: string; contacted_on: string; rung: number; rung_name: string | null; channel: string; outcome: string }[];
  replies: {
    id: string; received_on: string; channel: string; text: string; intent: string;
    suggested_intent: string | null; suggested_by: "ai" | "rules" | null; recorded_by: string; promised_date: string | null;
  }[];
};

export type DecisionKind = "send" | "wait" | "handoff" | "stop" | "payment_plan" | "counter_settle";

export type Decision = {
  invoice_id: string; invoice_number: string;
  buyer: { id: string; name: string; profile: string; language_pref: string };
  outstanding_paise: number; days_overdue: number; interest_paise: number;
  kind: DecisionKind; rung: number; rung_name: string; reason: string; source: string;
  available_rung: number; next_review_date: string | null;
  samadhaan: { ready: boolean; blockers: string[]; warnings: string[] } | null;
};

export type DecisionDetail = Decision & {
  draft: { subject: string; body: string; language: string; fallback_used: boolean; source: string } | null;
  legal: Legal;
  score: Score;
};

export type Dashboard = {
  as_of: string;
  totals: {
    receivable_paise: number; overdue_paise: number; overdue_invoices: number; open_invoices: number;
    interest_accrued_paise: number; disputed_paise: number; buyers: number;
    avg_days_overdue: number; collected_12w_paise: number;
  };
  aging: { bucket: string; count: number; paise: number }[];
  top_buyers: { buyer_id: string; name: string; overdue_paise: number; invoices: number; oldest_days: number; interest_paise: number; score: number; confidence: string }[];
  collections: { week_of: string; paise: number }[];
  due_soon: { id: string; invoice_number: string; buyer: string; outstanding_paise: number; due_date: string; days_to_due: number }[];
  early_warnings: { invoice_number: string; buyer: string; outstanding_paise: number; days_until_due: number; risk_band: "watch" | "high"; reasons: string[] }[];
};

export type AuditEntry = {
  id: string; at: string; actor: string; action: string;
  invoice_number: string | null; buyer_name: string | null;
  reason: string; source: "rule" | "llm" | "user"; detail: Record<string, unknown>;
};

export type BusinessProfile = {
  id: string; legal_name: string; udyam_registration: string | null;
  enterprise_class: "micro" | "small" | "medium";
  gstin: string | null; pan: string | null;
  address_line1: string | null; address_line2: string | null;
  city: string | null; state: string | null; pincode: string | null;
  contact_name: string | null; contact_email: string | null; contact_phone: string | null;
  role: Business["role"]; udyam_on_file: boolean; msmed_covered: boolean;
};
