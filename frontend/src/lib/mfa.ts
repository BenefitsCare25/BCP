/**
 * Second-factor input handling, shared by the HR and portal sign-in and
 * set-password forms.
 *
 * The MFA endpoints accept EITHER a 6-digit TOTP code OR a single-use recovery
 * code (`hash_recovery_code` lowercases and trims, so `a1b2-c3d4-e5f6`). The
 * forms used to run `value.replace(/\D/g, "")` with `maxLength={6}`, which
 * silently turned a pasted recovery code into five stray digits and then
 * disabled the submit button — so the one credential that exists for "I lost my
 * phone" could never actually be entered, making a lost authenticator a
 * permanent lockout.
 */

/** Server-side cap (`MfaVerifyIn.code` / `MemberMfaIn.code`). */
export const MFA_CODE_MAX_LENGTH = 16;

/** Keep what either form can legitimately contain; drop spaces and anything else. */
export function normalizeMfaCode(raw: string): string {
  return raw.replace(/[^0-9a-zA-Z-]/g, "").slice(0, MFA_CODE_MAX_LENGTH);
}

/**
 * Enough to be worth submitting: a full 6-digit TOTP, or a recovery code (which
 * is longer). Deliberately permissive — the server is the authority, and a
 * client-side rule that is stricter than the server locks people out.
 */
export function canSubmitMfaCode(code: string): boolean {
  return code.trim().length >= 6;
}
