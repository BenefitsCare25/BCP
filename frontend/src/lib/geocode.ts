/** Forward-geocoding via Singapore's OneMap search API (free, no key,
 * CORS-open). Covers SG postal codes, addresses and building names — JB /
 * Malaysia locations are not resolvable here; the locator falls back to GPS
 * for those. */

export interface GeocodedPoint {
  lat: number;
  lng: number;
  /** Human-readable resolved address, shown as the active-origin chip. */
  label: string;
}

interface OneMapResult {
  ADDRESS?: string;
  SEARCHVAL?: string;
  LATITUDE?: string;
  LONGITUDE?: string;
}

const ONEMAP_SEARCH = "https://www.onemap.gov.sg/api/common/elastic/search";

/** The one sentence for every failed lookup. A network error, a timeout and an
 *  unreadable reply mean the same thing to the member, and the browser's own
 *  wording for them is neither readable nor translatable. */
const LOOKUP_UNAVAILABLE = "Location lookup is unavailable right now — try again.";

async function searchOneMap(url: string): Promise<{ results?: OneMapResult[] }> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(8_000) });
    if (res.ok) return (await res.json()) as { results?: OneMapResult[] };
  } catch {
    // Reported below as the same unavailable lookup.
  }
  throw new Error(LOOKUP_UNAVAILABLE);
}

export async function geocodeSingapore(
  query: string,
): Promise<GeocodedPoint | null> {
  const q = query.trim();
  if (!q) return null;
  const url = `${ONEMAP_SEARCH}?searchVal=${encodeURIComponent(q)}&returnGeom=Y&getAddrDetails=Y&pageNum=1`;
  const body = await searchOneMap(url);
  const hit = body.results?.[0];
  if (!hit?.LATITUDE || !hit.LONGITUDE) return null;
  const lat = Number(hit.LATITUDE);
  const lng = Number(hit.LONGITUDE);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return { lat, lng, label: hit.ADDRESS || hit.SEARCHVAL || q };
}
