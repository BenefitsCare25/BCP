/** Platform console activity — per-broker counters for the master admin.
 *
 *  Aggregates only: counts and timestamps, never a person. Counters read from a
 *  firm's own schema (claims, enrolments, AI tokens) are `null` when that
 *  schema could not be read. Periods are whole UTC days ending today. */
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "./client";

export type ActivityPeriod = 7 | 30 | 90;
export const ACTIVITY_PERIODS: ActivityPeriod[] = [7, 30, 90];

export interface SignInCounts {
  staff: number;
  hr: number;
  member: number;
}

export interface ActivityCounters {
  companies: number;
  companies_portal_enabled: number;
  staff_active: number;
  staff_invited: number;
  hr_users: number;
  members_active: number;
  sign_ins: SignInCounts;
  /** Live sessions seen in the last 30 minutes. */
  active_sessions: number;
  claims_submitted: number | null;
  /** Live and not yet settled, whenever submitted. */
  claims_open: number | null;
  enrolments_submitted: number | null;
  ai_tokens: number | null;
}

export interface FirmActivity extends ActivityCounters {
  firm_id: string;
  name: string;
  slug: string | null;
  status: string;
  is_platform_owner: boolean;
  /** Latest sign-in, claim or enrolment submission, any time. */
  last_activity_at: string | null;
}

export interface ActivityTotals extends ActivityCounters {
  firms: number;
  /** Firms with a sign-in, claim or enrolment submission in the period. */
  firms_with_activity: number;
  /** Firms whose claim, enrolment and AI counters could not be read. */
  firms_unavailable: number;
}

export interface DailyActivity {
  /** UTC calendar day, YYYY-MM-DD. */
  date: string;
  sign_ins: number;
  claims_submitted: number | null;
  enrolments_submitted: number | null;
}

export interface PlatformActivity {
  generated_at: string;
  days: ActivityPeriod;
  totals: ActivityTotals;
  firms: FirmActivity[];
  daily: DailyActivity[];
}

export interface FirmDailyActivity {
  firm_id: string;
  days: ActivityPeriod;
  daily: DailyActivity[];
}

/** The server caches the platform view for two minutes. */
const STALE_MS = 120_000;

export function usePlatformActivity(days: ActivityPeriod) {
  return useQuery({
    queryKey: ["platform", "activity", days],
    queryFn: () => api.get<PlatformActivity>(`/platform/activity?days=${days}`),
    staleTime: STALE_MS,
    placeholderData: keepPreviousData,
  });
}

export function useFirmDailyActivity(firmId: string, days: ActivityPeriod) {
  return useQuery({
    queryKey: ["platform", "firms", firmId, "activity", days],
    queryFn: () =>
      api.get<FirmDailyActivity>(
        `/platform/firms/${encodeURIComponent(firmId)}/activity?days=${days}`,
      ),
    staleTime: STALE_MS,
    // Keep the previous period while the next loads, never another firm's.
    placeholderData: (previous) => (previous?.firm_id === firmId ? previous : undefined),
  });
}

export function signInTotal(counts: SignInCounts): number {
  return counts.staff + counts.hr + counts.member;
}

/** A counter as text: "—" when it could not be read. */
export function fmtCount(value: number | null | undefined): string {
  return value == null ? "—" : value.toLocaleString();
}
