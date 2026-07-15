/** Trigger a browser download for a Blob, then release the object URL. */
export function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** The server-chosen filename from a Content-Disposition header — honors the
 * RFC 5987 `filename*=UTF-8''…` form first, then plain `filename=`. */
export function filenameFromDisposition(
  header: string | null,
  fallback: string,
): string {
  if (!header) return fallback;
  const utf8 = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1].trim());
    } catch {
      // malformed encoding — fall through to the plain form
    }
  }
  const plain = header.match(/filename="?([^";]+)"?/i);
  return plain ? plain[1].trim() : fallback;
}

/** Download a fetched Response as a file, preferring the server's
 * Content-Disposition filename over the caller's fallback. */
export async function downloadResponseAsFile(
  res: Response,
  fallbackFilename: string,
): Promise<void> {
  const filename = filenameFromDisposition(
    res.headers.get("Content-Disposition"),
    fallbackFilename,
  );
  triggerDownload(await res.blob(), filename);
}
