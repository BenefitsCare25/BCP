/** No-login link pages: the shared employee enrolment form and the HR
 *  download. Plain `fetch`, never the portal/HR clients — those redirect to a
 *  sign-in page on a 401, and these pages have no account to sign in to.
 *
 *  The capability lives in the URL fragment (it never reaches a server log)
 *  and is sent as the `X-Inspro-Link` header. After the first read the page
 *  keeps it in this tab's session storage and removes it from the address bar,
 *  so a refresh still works but the link is not left in browser history. */
import { useEffect, useState } from "react";
import type { ProductTierSet } from "@/api/enrollment";
import type {
  FormClause,
  FormRule,
  PlanFact,
  ProductContribution,
} from "@/api/enrollmentForms";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";
const LINK_HEADER = "X-Inspro-Link";

export class LinkError extends Error {
  status: number;
  code?: string;
  problems: string[];
  attemptsLeft?: number;
  state?: string;
  constructor(status: number, message: string, detail: Record<string, unknown> = {}) {
    super(message);
    this.name = "LinkError";
    this.status = status;
    this.code = typeof detail.code === "string" ? detail.code : undefined;
    this.problems = Array.isArray(detail.problems) ? detail.problems.map(String) : [];
    this.attemptsLeft = typeof detail.attempts_left === "number" ? detail.attempts_left : undefined;
    this.state = typeof detail.state === "string" ? detail.state : undefined;
  }
}

async function failure(res: Response): Promise<LinkError> {
  let detail: Record<string, unknown> = {};
  let message = res.status === 429
    ? "Too many attempts. Wait a minute and try again."
    : "Something went wrong. Please try again.";
  try {
    const body = await res.json();
    const raw = body?.detail;
    if (typeof raw === "string") message = raw;
    else if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      detail = raw as Record<string, unknown>;
      if (typeof detail.message === "string") message = detail.message;
    } else if (Array.isArray(raw)) {
      message = "Some details are missing or not in the expected format.";
    }
  } catch { /* Non-JSON error: keep the generic message. */ }
  return new LinkError(res.status, message, detail);
}

async function call(
  path: string,
  token: string | null,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (token) headers.set(LINK_HEADER, token);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init, headers, credentials: "same-origin", cache: "no-store",
    });
  } catch {
    throw new LinkError(0, "No connection. Check your internet and try again.");
  }
  if (!res.ok) throw await failure(res);
  return res;
}

/** Retry a busy server (429) a few times with growing waits — a whole
 *  office opens the QR at once — and transient faults once; never a 4xx. */
export function retryLinkQuery(count: number, error: unknown): boolean {
  if (error instanceof LinkError && error.status === 429) return count < 4;
  return !(error instanceof LinkError && error.status >= 400 && error.status < 500) && count < 2;
}

export function retryLinkDelay(attempt: number): number {
  return Math.min(8000, 1500 * 2 ** attempt);
}

/** Read the link token once: from the fragment on first open, then from this
 *  tab's storage. The fragment is removed from the address bar. */
export function takeLinkToken(storageKey: string): string | null {
  const fromHash = window.location.hash.replace(/^#/, "").trim();
  if (/^[A-Za-z0-9_-]{32,64}$/.test(fromHash)) {
    try { sessionStorage.setItem(storageKey, fromHash); } catch { /* Memory only. */ }
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    return fromHash;
  }
  try { return sessionStorage.getItem(storageKey); } catch { return null; }
}

/** The link token for this page, kept current when another link is opened in
 *  the same tab (only the fragment changes, so the page does not reload). */
export function useLinkToken(storageKey: string): string | null {
  const [token, setToken] = useState(() => takeLinkToken(storageKey));
  useEffect(() => {
    const onHash = () => { if (window.location.hash) setToken(takeLinkToken(storageKey)); };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [storageKey]);
  return token;
}

// ── Employee form ────────────────────────────────────────────────────────────

export type PublicFormStatus = "open" | "not_yet_open" | "closed" | "paused";

export interface PublicCohort {
  category_id: string;
  label: string;
  cohort: string;
  tier_set: ProductTierSet;
}

export interface PublicProduct {
  product_code: string;
  product_name: string | null;
  insurer: string | null;
  cohorts: PublicCohort[];
  age_limits: Partial<Record<"spouse" | "child", { min?: number; max?: number }>>;
  gst_included: boolean;
}

export interface PublicDocument {
  id: string;
  label: string;
  url: string | null;
  has_file: boolean;
}

export interface PublicForm {
  status: PublicFormStatus;
  company_name: string;
  window_name: string;
  closes_at: string;
  opens_at: string;
  title: string | null;
  window_type: string | null;
  policy_start: string | null;
  policy_end: string | null;
  intro_lines: string[];
  helpline: string | null;
  submission_note: string | null;
  eligibility_notes: string[];
  clauses: FormClause[];
  documents: PublicDocument[];
  rules: FormRule[];
  products: PublicProduct[];
  automatic: string[];
  contributions: ProductContribution[];
  plans: PlanFact[];
  allow_dependant_changes: boolean;
  max_children: number;
}

export type DependantAction = "add" | "withdraw" | "update";
export type ProductAction = "no_change" | "enrol" | "withdraw";

export interface DependantInput {
  key: string;
  action: DependantAction;
  relationship: "spouse" | "child";
  name: string;
  gender: "M" | "F" | "";
  id_no: string;
  dob: string;
  occupation: string;
  nationality: string;
  residence: string;
  child_confirmed: boolean;
}

export interface ProductChoiceInput {
  product_code: string;
  category_id: string;
  action: ProductAction | "";
  tier_key: string;
  cover_self: boolean;
  covered: string[];
}

export interface ParticularsInput {
  name: string;
  id_no: string;
  staff_id: string;
  gender: "M" | "F" | "";
  dob: string;
  contact_no: string;
  email: string;
  job_grade: string;
  date_of_hire: string;
  marital_status: "" | "single" | "married" | "divorced" | "widowed";
}

export interface PublicReceipt {
  reference_no: string;
  submitted_at: string;
  copy_key: string;
  copy_expires_at: string;
}

export async function fetchPublicForm(token: string): Promise<PublicForm> {
  return (await call("/public/enrol", token)).json();
}

export async function submitPublicForm(
  token: string,
  body: Record<string, unknown>,
): Promise<PublicReceipt> {
  return (await call("/public/enrol/submissions", token, {
    method: "POST", body: JSON.stringify(body),
  })).json();
}

export async function fetchPublicDocument(token: string, documentId: string): Promise<Response> {
  return call(`/public/enrol/documents/${encodeURIComponent(documentId)}`, token);
}

export async function fetchOwnCopy(
  token: string,
  referenceNo: string,
  copyKey: string,
): Promise<Response> {
  return call("/public/enrol/copy", token, {
    method: "POST", body: JSON.stringify({ reference_no: referenceNo, copy_key: copyKey }),
  });
}

// ── HR download ──────────────────────────────────────────────────────────────

export type HrDownloadState =
  | "ready" | "verified" | "active" | "expired" | "locked" | "revoked" | "in_use";

export interface HrDownload {
  state: HrDownloadState;
  company_name: string;
  label: string;
  file_count: number;
  size_bytes: number;
  server_time: string;
  expires_at: string | null;
  access_seconds: number;
  attempts_left: number | null;
  downloads_left: number | null;
}

export const HR_DOWNLOAD_FILE_URL = `${API_BASE}/public/hr-download/file`;

export async function fetchHrDownload(token: string): Promise<HrDownload> {
  return (await call("/public/hr-download", token)).json();
}

export async function enterHrPin(token: string, pin: string): Promise<HrDownload> {
  return (await call("/public/hr-download/pin", token, {
    method: "POST", body: JSON.stringify({ pin }),
  })).json();
}

export async function startHrDownload(token: string): Promise<HrDownload> {
  return (await call("/public/hr-download/start", token, { method: "POST" })).json();
}
