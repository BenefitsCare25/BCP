export type Scope = "company" | "firm";
export type Audience = "employee" | "hr";
export type Purpose = "general" | "invitation" | "password_reset";
export interface Content {
  title: string; audience: Audience; purpose: Purpose; subject: string; preheader: string;
  body: string; button_label: string; button_url: string;
}
export interface Template {
  key: string; content: Content; revision: number; published_content: Content | null;
  published_version: string | null; source: string; has_local_draft: boolean;
  has_changes: boolean; updated_at: string | null;
}
export interface Catalog {
  items: Template[]; placeholders: Record<string, string>; delivery_enabled: boolean;
  delivery_reason: string; company_name: string | null;
}
export interface Recipient {
  id: string; name: string; staff_id: string; email: string; status: string;
  eligible: boolean; reason: string; login_identifier: string; account_required: boolean;
}
export interface RecipientPage { items: Recipient[]; total: number; eligible_ids: string[] }
export interface Preview {
  subject: string; preheader: string; html: string; text: string; errors: Record<string, string>;
  warnings: string[]; values: Record<string, string>; data_source: "sample" | "real";
  recipient_email: string | null; valid: boolean; activation_link_generated: false;
}
export interface BrandingContent {
  sender_display_name: string; support_email: string; footer: string; logo_url: string;
}
export interface Branding { content: BrandingContent; revision: number; source: string; has_override: boolean }
export interface Review {
  recipients: Recipient[]; eligible_count: number; excluded_count: number;
  account_creation_count: number; template_version: string; review_token: string;
  delivery_enabled: boolean; delivery_reason: string;
}
export const BLANK: Content = { title: "", audience: "employee", purpose: "general", subject: "",
  preheader: "", body: "", button_label: "", button_url: "" };
export const PURPOSE_LABELS: Record<Purpose, string> = {
  general: "General message", invitation: "Portal invitation", password_reset: "Password setup / reset",
};
export const SOURCE_LABELS: Record<string, string> = {
  company: "Company override", firm: "Broker default", builtin: "Inspro starter", draft: "Draft",
};
export const fieldClass = "w-full min-w-0 rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60";
export const apiPath = (scope: Scope, suffix = "") => `/email-templates${suffix}?scope=${scope}`;
// Bind requests to the view that started them, including while auth refreshes.
export const clientOptions = (clientId: string | null): RequestInit => ({ headers: { "X-Inspro-Client": clientId ?? "" } });
