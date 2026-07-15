/** Render any caught value as a human-readable string. */
export function formatError(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "An unexpected error occurred.";
}

/** A non-OK API response with its HTTP status preserved, so callers can
 * branch on status codes (404 → empty state) instead of message substrings. */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** HTTP status of a caught value, when it carries one. */
export function errorStatus(error: unknown): number | null {
  if (error instanceof ApiError) return error.status;
  if (error instanceof ConflictDetailError) return 409;
  return null;
}

/** True when the caught value is an API 404 ("not found" / no-resource). */
export function isNotFoundError(error: unknown): boolean {
  return errorStatus(error) === 404;
}

/** A 409 whose FastAPI `detail` carries a machine-readable `code` — a business
 * rule the UI can react to (offer an acknowledge/override choice) instead of a
 * flat error toast. `message` still renders via formatError for generic paths.
 * Lives here (not api/client.ts) because both the broker and portal fetch
 * wrappers throw it. */
export interface ConflictDetail {
  code: string;
  message?: string;
  [key: string]: unknown;
}

export class ConflictDetailError extends Error {
  detail: ConflictDetail;
  constructor(detail: ConflictDetail) {
    super(
      typeof detail.message === "string" && detail.message
        ? detail.message
        : "The request conflicts with the current state.",
    );
    this.name = "ConflictDetailError";
    this.detail = detail;
  }
}

/** Build the Error for a non-OK response body, promoting coded 409 conflicts
 * to ConflictDetailError. */
export function errorFromText(
  status: number,
  text: string,
  statusText: string,
): Error {
  if (status === 409 && text) {
    try {
      const detail = (JSON.parse(text) as { detail?: unknown }).detail;
      if (
        detail &&
        typeof detail === "object" &&
        typeof (detail as { code?: unknown }).code === "string"
      ) {
        return new ConflictDetailError(detail as ConflictDetail);
      }
    } catch {
      // not JSON — fall through to the plain-text error
    }
  }
  return new ApiError(parseErrorText(text, statusText), status);
}

/** One FastAPI validation item ({loc, msg, type}) → its message. */
function msgFromItem(item: unknown): string {
  if (item && typeof item === "object" && "msg" in item) {
    return String((item as { msg: unknown }).msg);
  }
  return String(item);
}

/**
 * Render FastAPI's `detail` (string, `{message, errors[]}`, or a 422 array of
 * validation items) as a readable string — never the literal "[object Object]".
 */
function stringifyDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map(msgFromItem).filter(Boolean).join("; ");
  }
  if (detail && typeof detail === "object") {
    const o = detail as { message?: unknown; errors?: unknown };
    const parts: string[] = [];
    if (typeof o.message === "string") parts.push(o.message);
    if (Array.isArray(o.errors)) parts.push(o.errors.map(String).join("; "));
    if (parts.length) return parts.join(" ");
    try {
      return JSON.stringify(detail);
    } catch {
      return "Request failed";
    }
  }
  return String(detail);
}

/** Like `parseResponseError`, for a body that has already been read. */
export function parseErrorText(text: string, statusText: string): string {
  if (!text) return statusText;
  try {
    const body = JSON.parse(text) as unknown;
    if (typeof body === "object" && body && "detail" in body) {
      return stringifyDetail((body as { detail: unknown }).detail);
    }
    return text;
  } catch {
    return text;
  }
}

/**
 * Extract a useful message from a non-OK fetch Response. Tries to parse
 * FastAPI's `{ detail: ... }` shape, falls back to raw body, then statusText.
 */
export async function parseResponseError(res: Response): Promise<string> {
  return parseErrorText(await res.text(), res.statusText);
}
