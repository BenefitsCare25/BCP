/** Typed calls + query hooks for the HR credential surface. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { hrApi } from "@/api/hrClient";
import type { HrMe } from "@/stores/hrSession";
import { useHrSession } from "@/stores/hrSession";

export interface HrTokenResult {
  status: "authenticated";
  access_token: string;
  expires_at: string;
  mfa_enrollment_required: boolean;
  me: HrMe;
}

export interface HrChallengeResult {
  status: "mfa_required" | "password_reset_required";
  challenge_token: string;
}

export type HrLoginResult = HrTokenResult | HrChallengeResult;

export function isTokenResult(r: HrLoginResult): r is HrTokenResult {
  return r.status === "authenticated";
}

/** Persist a token result into the session store. */
export function adoptSession(result: HrTokenResult): void {
  useHrSession
    .getState()
    .setSession(
      result.access_token,
      result.expires_at,
      result.me,
      result.mfa_enrollment_required,
    );
}

export function useHrLogin() {
  return useMutation({
    mutationFn: (body: { identifier: string; password: string }) =>
      hrApi.postPublic<HrLoginResult>("/hr/auth/login", body),
  });
}

export function useHrMfa() {
  return useMutation({
    mutationFn: (body: { challenge_token: string; code: string }) =>
      hrApi.postPublic<HrTokenResult>("/hr/auth/mfa", body),
  });
}

export function useHrSetPassword() {
  return useMutation({
    mutationFn: (body: { token: string; password: string }) =>
      hrApi.postPublic<HrTokenResult>("/hr/auth/set-password", body),
  });
}

export function useHrMe() {
  const token = useHrSession((s) => s.token);
  return useQuery({
    queryKey: ["hr-me", token],
    queryFn: () => hrApi.get<HrMe>("/hr/auth/me"),
    enabled: !!token,
    staleTime: 60_000,
  });
}

export interface MfaStart {
  secret: string;
  otpauth_uri: string;
}

export function useHrMfaEnrollStart() {
  return useMutation({
    mutationFn: () => hrApi.post<MfaStart>("/hr/auth/mfa/enroll/start", {}),
  });
}

export function useHrMfaEnrollConfirm() {
  // Deliberately does NOT invalidate hr-me here: the recovery codes must stay
  // on screen until the user acknowledges them. The caller refetches hr-me when
  // the user clicks "I've saved them" (which flips the page to the enrolled view).
  return useMutation({
    mutationFn: (code: string) =>
      hrApi.post<{ status: string; recovery_codes: string[] }>(
        "/hr/auth/mfa/enroll/confirm",
        { code },
      ),
  });
}

export function useHrMfaDisable() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (password: string) =>
      hrApi.post<{ status: string }>("/hr/auth/mfa/disable", { password }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hr-me"] }),
  });
}
