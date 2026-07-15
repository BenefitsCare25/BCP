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

export async function geocodeSingapore(
  query: string,
): Promise<GeocodedPoint | null> {
  const q = query.trim();
  if (!q) return null;
  const url = `${ONEMAP_SEARCH}?searchVal=${encodeURIComponent(q)}&returnGeom=Y&getAddrDetails=Y&pageNum=1`;
  const res = await fetch(url, { signal: AbortSignal.timeout(8_000) });
  if (!res.ok) {
    throw new Error("Location lookup is unavailable right now — try again.");
  }
  const body = (await res.json()) as { results?: OneMapResult[] };
  const hit = body.results?.[0];
  if (!hit?.LATITUDE || !hit.LONGITUDE) return null;
  const lat = Number(hit.LATITUDE);
  const lng = Number(hit.LONGITUDE);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return { lat, lng, label: hit.ADDRESS || hit.SEARCHVAL || q };
}
