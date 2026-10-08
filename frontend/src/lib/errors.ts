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
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/** HTTP status of a caught value, when it carries one. */
export function errorStatus(error: unknown): number | null {
  if (error instanceof ApiError) return error.status;
  if (error instanceof ConflictDetailError) return 409;
  return null;
}

export function errorCode(error: unknown): string | undefined {
  return error instanceof ApiError ? error.code : undefined;
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

/** 409 `firm_origin_unavailable`: the broker has no active web address, so
 * nothing that emails a sign-in or set-password link can be sent. Retrying
 * cannot help until an address is active, so send flows close their
 * confirmation and show the server's message. */
export function isFirmOriginUnavailable(error: unknown): boolean {
  return (
    (error instanceof ConflictDetailError &&
      error.detail.code === "firm_origin_unavailable") ||
    (error instanceof ApiError && error.code === "firm_origin_unavailable")
  );
}

/** The message to show for a refused send: the server's own sentence, with
 * the contract's wording when a proxy stripped it. */
export function sendErrorMessage(error: unknown): string {
  if (isFirmOriginUnavailable(error)) {
    const message = formatError(error);
    return message && message !== "The request conflicts with the current state."
      ? message
      : "This broker has no active web address yet.";
  }
  return formatError(error);
}

export function isStaleConfigurationError(error: unknown): boolean {
  return (
    error instanceof ConflictDetailError &&
    error.detail.code === "stale_configuration"
  );
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
      // A coded conflict arrives as FastAPI's `{detail: {code, message}}`, or
      // as a bare `{code, message}` body from middleware.
      const body = JSON.parse(text) as { detail?: unknown } | null;
      const detail = body && typeof body === "object" && "detail" in body ? body.detail : body;
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
  // A message is NOT guaranteed: when the server dies mid-request the gateway
  // answers with an empty body, and HTTP/2 carries no reason phrase, so both
  // inputs are "". That rendered as a red banner with nothing written in it —
  // the failure looked silent to the user while the server log had the cause.
  // The status alone is enough to make it reportable.
  const message =
    parseErrorText(text, statusText) || `Request failed (HTTP ${status})`;
  let code: string | undefined;
  try {
    const detail = (JSON.parse(text) as { detail?: { code?: unknown } }).detail;
    if (typeof detail?.code === "string") code = detail.code;
  } catch { /* A gateway response need not be JSON. */ }
  return new ApiError(message, status, code);
}

/** The parsed JSON body of a refusal, or null when it is not JSON (a proxy's
 *  HTML page, an empty body). */
function jsonBody(text: string): { detail?: unknown; code?: unknown } | null {
  try {
    const body: unknown = JSON.parse(text);
    return body && typeof body === "object" ? (body as { detail?: unknown; code?: unknown }) : null;
  } catch {
    return null;
  }
}

/** The server states its own limit — 50 MB for a whole request ("Uploads are
 *  limited to 50 MB per request."), less for some single files ("File exceeds
 *  15 MB") — so the number is read from its sentence, never assumed. A proxy's
 *  413 states none and arrives as markup or nothing, so it gets no number. */
function tooLargeMessage(text: string): string {
  const detail = jsonBody(text)?.detail;
  if (typeof detail !== "string" || !detail.trim()) {
    return "That upload is too large. Use a smaller file, or fewer files at once, and try again.";
  }
  const limit = /(\d+(?:\.\d+)?)\s*MB/i.exec(detail)?.[1];
  if (!limit) return `That upload is too large. ${detail.trim()}`;
  return /per request/i.test(detail)
    ? `That upload is too large — uploads are limited to ${limit} MB at a time. Use smaller files, or send fewer at once, and try again.`
    : `That file is too large — the limit is ${limit} MB. Choose a smaller file and try again.`;
}

/** "Try again …" from a `Retry-After` header in seconds; HTTP dates and
 *  absent headers fall back to a plain "in a moment". */
function retryHint(retryAfter: string | null): string {
  const seconds = Number(retryAfter);
  if (!retryAfter || !Number.isFinite(seconds) || seconds <= 0) {
    return "Try again in a moment.";
  }
  if (seconds <= 60) return `Try again in about ${Math.ceil(seconds)} seconds.`;
  const minutes = Math.ceil(seconds / 60);
  return `Try again in about ${minutes} minute${minutes === 1 ? "" : "s"}.`;
}

/** The two upload refusals that need their own sentence, or null for any other.
 *
 * - **413**: the file (or the whole request) is over a size limit. It can come
 *   from a proxy in front of the API with an HTML or empty body, which used to
 *   reach the person as raw markup or "Request failed".
 * - **503 `document_scanner_busy`**: the malware scanner had no free slot and
 *   the file was not stored. `Retry-After` says when a slot is likely.
 *
 * Neither is retried automatically; the person decides when to send the file
 * again. */
export function uploadRefusal(
  status: number,
  text: string,
  retryAfter: string | null,
): ApiError | null {
  if (status === 413) return new ApiError(tooLargeMessage(text), 413);
  if (status !== 503) return null;
  const body = jsonBody(text);
  const detail = body?.detail;
  const code =
    detail && typeof detail === "object" ? (detail as { code?: unknown }).code : body?.code;
  if (code !== "document_scanner_busy") return null;
  return new ApiError(
    `The document scanner is busy, so your file wasn't uploaded. ${retryHint(retryAfter)}`,
    503,
    "document_scanner_busy",
  );
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
