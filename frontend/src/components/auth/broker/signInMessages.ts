/** What to tell broker staff when a sign-in step is refused. Wrong credentials
 *  stay generic, so the page never confirms whether an account exists; lockout
 *  (423), throttling (429) and outages are stated distinctly, because retrying
 *  against any of them only extends the wait. */
import { ApiError, errorCode, formatError } from "@/lib/errors";

const UNREACHABLE =
  "We couldn't reach the sign-in service, so nothing was checked. Check your connection and try again.";
const UNAVAILABLE = "Sign-in is unavailable right now. Try again in a moment.";
const LOCKED =
  "This account is locked for a while after too many attempts. Try again later, or contact your administrator.";
const THROTTLED = "Too many attempts. Wait a minute, then try again.";

export const INVITATION_INVALID =
  "This invitation can't be used. It may have expired, been revoked or already been accepted. Ask your administrator to invite you again.";

function serverSentence(error: ApiError, fallback: string): string {
  const message = formatError(error).trim();
  return message && !/^Sign-in could not be completed/i.test(message) ? message : fallback;
}

/** Shared by every step: an outage or an unreachable server, a lockout, a
 *  throttle. Null when the refusal is specific to the step. */
function commonRefusal(error: unknown): string | null {
  if (!(error instanceof ApiError)) return UNREACHABLE;
  if (error.status === 423) return serverSentence(error, LOCKED);
  if (error.status === 429) return THROTTLED;
  if (error.status >= 500) return UNAVAILABLE;
  return null;
}

export function passwordSignInError(error: unknown): string {
  const common = commonRefusal(error);
  if (common) return common;
  if (errorCode(error) === "sign_in_method_disabled") {
    return "Email and password sign-in is turned off for this organisation. Use another sign-in option, or contact your administrator.";
  }
  const status = (error as ApiError).status;
  if (status === 401 || status === 400 || status === 422) {
    return "That email and password didn't work. Check them and try again.";
  }
  return serverSentence(error as ApiError, UNAVAILABLE);
}

/** A refused authenticator code, and whether the password step must be
 *  repeated because the five-minute challenge has ended. */
export function codeError(error: unknown): { message: string; restart: boolean } {
  const common = commonRefusal(error);
  if (common) return { message: common, restart: false };
  const apiError = error as ApiError;
  const expired =
    errorCode(error) === "challenge_expired" || /expired/i.test(formatError(error));
  if (apiError.status === 401 && expired) {
    return {
      message: "Your sign-in timed out. Enter your password again to get a new code request.",
      restart: true,
    };
  }
  if (apiError.status === 401 || apiError.status === 422) {
    return {
      message: "That code didn't work. Enter the current code from your authenticator app, or an unused recovery code.",
      restart: false,
    };
  }
  return { message: serverSentence(apiError, UNAVAILABLE), restart: false };
}

/** A refused invitation acceptance. `invalid` means the invitation itself is
 *  unusable; a password-policy refusal keeps the form open with its reason. */
export function inviteError(error: unknown): { message: string; invalid: boolean; field: boolean } {
  const common = commonRefusal(error);
  if (common) return { message: common, invalid: false, field: false };
  const apiError = error as ApiError;
  if (errorCode(error) === "invitation_invalid") {
    return { message: serverSentence(apiError, INVITATION_INVALID), invalid: true, field: false };
  }
  if (apiError.status === 422) {
    return {
      message: serverSentence(apiError, "Choose a stronger password and try again."),
      invalid: false,
      field: true,
    };
  }
  return { message: serverSentence(apiError, UNAVAILABLE), invalid: false, field: false };
}

/** Refusals handed over from the Microsoft redirect at boot. */
export function signInNoticeMessage(notice: { code: string; message?: string }): string {
  if (notice.code === "invitation_invalid") {
    return notice.message && !/^Sign-in could not be completed/i.test(notice.message)
      ? notice.message
      : INVITATION_INVALID;
  }
  if (notice.code === "sign_in_method_disabled") {
    return "Microsoft sign-in is turned off for this organisation. Use another sign-in option, or contact your administrator.";
  }
  return notice.message ?? UNAVAILABLE;
}
