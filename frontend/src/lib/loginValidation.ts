import { errorCode, errorStatus, formatError } from "./errors";

export const IDENTIFIER_MAX_LENGTH = 320;
export const PASSWORD_MAX_LENGTH = 256;
export type LoginFieldErrors = { company?: string; identifier?: string; password?: string };

export function validateCredentials(identifier: string, password: string, company?: string): LoginFieldErrors {
  const errors: LoginFieldErrors = {};
  if (!identifier.trim()) errors.identifier = "Enter your email or login ID.";
  else if (identifier.trim().length > IDENTIFIER_MAX_LENGTH) errors.identifier = "Use no more than 320 characters.";
  if (!password) errors.password = "Enter your password.";
  else if (password.length > PASSWORD_MAX_LENGTH) errors.password = "Use no more than 256 characters.";
  if (company !== undefined && (company.trim().length > 63 || !/^(?!-)(?!.*--)[a-z0-9-]+(?<!-)$/i.test(company.trim()))) {
    errors.company = "Enter the company code from your invitation.";
  }
  return errors;
}

export function loginError(error: unknown): string {
  const status = errorStatus(error);
  if (errorCode(error) === "invite_expired" || status === 423 || status === 429 || status === 403 || status === 404) return formatError(error);
  if (status === 401 || status === 400) return "Those details weren't recognised. Check and try again.";
  if (status === 422) return "Check the entered details and try again.";
  return "We couldn't reach the portal just now. Your details weren't checked. Try again in a moment.";
}
