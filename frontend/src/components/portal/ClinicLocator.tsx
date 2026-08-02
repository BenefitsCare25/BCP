/** Find a clinic — the one portal screen a member opens while already standing
 * somewhere, deciding where to walk.
 *
 * That framing decides everything. The page has TWO STATES and the previous
 * design pretended it had one:
 *
 *   **Unlocated** it is an alphabetical directory — "1 Aljunied Medical" first
 *   — which answers nothing, so the page's job is to ask one question and get
 *   out of the way. It does not pretend paging through 84 screens of A-to-Z is
 *   a way to find anything.
 *   **Located** it is a ranked answer, the question folds into a chip, and the
 *   list groups into distance bands, because "can I walk?" is what the figure
 *   is standing in for.
 *
 * **The list is a LEDGER, not a stack of cards** — one pane per group with
 * hairline-divided rows inside it, which is the construction
 * `leaf/ClaimMount.tsx` arrived at for exactly the same defect: every entry
 * carries the same handful of facts, so a pane each spent its height
 * repeating the shape of the one above it. A clinic cost ~270px and now costs
 * ~88; ten of them fit one screen instead of four.
 *
 * Shared between `/portal/clinics` and the broker employee-view preview
 * (PortalFrame); both render inside `.leaf`. The data hook is injected so each
 * surface fetches through its own auth client, and both endpoints return the
 * identical ClinicSearch shape — which is what stops the preview drifting from
 * what the member actually sees. */
import { useMemo, useRef, useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import type {
  Clinic,
  ClinicSearch,
  ClinicSearchParams,
  ClinicType,
  PanelCountry,
} from "@/api/panelListings";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import { Mount, glassSurface } from "@/components/portal/leaf/Mount";
import { actionClass } from "@/components/portal/leaf/Action";
import { ClinicRow } from "@/components/portal/clinics/ClinicRow";
import {
  ALL_AREAS,
  ClinicFinder,
  type OriginState,
  type OriginStatus,
} from "@/components/portal/clinics/ClinicFinder";
import { cn } from "@/lib/cn";
import { singaporeNow } from "@/lib/clinicHours";
import { readableCase } from "@/lib/clinicText";
import { formatError, isNotFoundError } from "@/lib/errors";
import { geocodeSingapore } from "@/lib/geocode";
import { useDebouncedValue } from "@/lib/use-debounced-value";

const PAGE_SIZE = 10;
/** `app/core/pagination.py::MAX_LIMIT`. Past it the page says so rather than
 * pretending to walk the rest of the panel ten at a time. */
const MAX_ROWS = 200;

/** "Can I walk there?", which is the question the distance figure stands in
 * for. Only rendered when there is more than one band — a single heading over
 * the only group names nothing its rows do not, the same rule the claims
 * ledger applies to its months. */
const BANDS: { max: number; label: string }[] = [
  { max: 1, label: "Within 1 km" },
  { max: 3, label: "1 – 3 km" },
  { max: 10, label: "3 – 10 km" },
  { max: Infinity, label: "More than 10 km" },
];

interface Group {
  key: string;
  label: string;
  items: Clinic[];
}

/** Consecutive runs only, which is safe because the server has already sorted
 * on the same figure we are grouping by. */
function bandGroups(items: Clinic[]): Group[] {
  const groups: Group[] = [];
  for (const clinic of items) {
    const band =
      clinic.distance_km === null
        ? { max: NaN, label: "Distance unknown" }
        : BANDS.find((b) => clinic.distance_km! < b.max) ?? BANDS[BANDS.length - 1];
    const last = groups[groups.length - 1];
    if (last && last.key === band.label) last.items.push(clinic);
    else groups.push({ key: band.label, label: band.label, items: [clinic] });
  }
  return groups;
}

export function ClinicLocator({
  useClinicsQuery,
}: {
  /** Injected data source — portal or broker-preview hook. */
  useClinicsQuery: (
    params: ClinicSearchParams,
  ) => UseQueryResult<ClinicSearch, Error>;
}) {
  const [typeKey, setTypeKey] = useState("all");
  const [area, setArea] = useState(ALL_AREAS);
  const [search, setSearch] = useState("");
  const [shown, setShown] = useState(PAGE_SIZE);
  const [origin, setOrigin] = useState<(OriginState & { lat: number; lng: number }) | null>(
    null,
  );
  const [status, setStatus] = useState<OriginStatus>({ kind: "idle" });

  const q = useDebouncedValue(search.trim(), 300);
  // One reading for the whole list, so no two rows disagree about "now". It is
  // recomputed on each render rather than ticked: a member does not sit on this
  // page watching a clinic close, and a timer here would re-render ten rows a
  // minute for a figure that changes twice a day.
  const clock = useMemo(() => singaporeNow(), []);

  const [typeCountry, clinicType] = useMemo(() => {
    if (typeKey === "all") return [undefined, undefined] as const;
    const [country, type] = typeKey.split(":");
    return [country as PanelCountry, type as ClinicType] as const;
  }, [typeKey]);

  const query = useClinicsQuery({
    clinic_type: clinicType,
    country: typeCountry,
    area: area === ALL_AREAS ? undefined : area,
    q: q || undefined,
    lat: origin?.lat,
    lng: origin?.lng,
    offset: 0,
    limit: shown,
  });

  const setNewOrigin = (next: OriginState & { lat: number; lng: number }) => {
    setOrigin(next);
    setStatus({ kind: "idle" });
    setShown(PAGE_SIZE);
  };

  const locateByGps = () => {
    if (!("geolocation" in navigator)) {
      setStatus({
        kind: "error",
        message:
          "Your browser doesn't support location — enter a postal code instead.",
      });
      return;
    }
    setStatus({ kind: "busy" });
    navigator.geolocation.getCurrentPosition(
      (pos) =>
        setNewOrigin({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          label: "Your current location",
          postal: null,
        }),
      (err) =>
        setStatus({
          kind: "error",
          message:
            err.code === err.PERMISSION_DENIED
              ? "Location access was denied — enter a postal code instead."
              : "We couldn't work out where you are — enter a postal code instead.",
        }),
      { enableHighAccuracy: true, timeout: 10_000, maximumAge: 60_000 },
    );
  };

  // Guards a stale lookup from overwriting a newer one — the postal box fires
  // on its own as soon as six digits are present, so two can be in flight.
  const lookupSeq = useRef(0);

  const locateByPostal = async (code: string) => {
    const seq = ++lookupSeq.current;
    setStatus({ kind: "busy" });
    try {
      const point = await geocodeSingapore(code);
      if (seq !== lookupSeq.current) return;
      if (point) {
        // OneMap answers in supplier capitals and repeats the code it was
        // given ("618 YISHUN RING ROAD SINGAPORE 760618"), which the chip is
        // already printing in bold beside it.
        setNewOrigin({
          ...point,
          postal: code,
          label: readableCase(point.label).replace(/\s*Singapore\s+\d{6}\s*$/i, ""),
        });
      } else {
        setStatus({
          kind: "error",
          message:
            "That postal code didn't resolve — check it, or use my location instead. (For Johor Bahru clinics, use your device location.)",
        });
      }
    } catch (error) {
      if (seq !== lookupSeq.current) return;
      setStatus({ kind: "error", message: formatError(error) });
    }
  };

  if (query.isLoading) return <LeafSkeleton label="Loading clinics" />;
  if (query.isError && !isNotFoundError(query.error)) {
    return <PortalErrorState onRetry={() => void query.refetch()} />;
  }

  const data = query.data;
  const facets = data?.filters.clinic_types ?? [];
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const located = data?.located ?? false;
  const filtersActive = typeKey !== "all" || area !== ALL_AREAS || q !== "";

  if (query.isError || facets.length === 0) {
    return (
      <Mount label="No panel clinics yet">
        <p className="text-row text-label">
          Your policy doesn't have a clinic network published yet. Your HR team
          can tell you where you're covered in the meantime.
        </p>
      </Mount>
    );
  }

  const groups = located
    ? bandGroups(items)
    : [{ key: "all", label: "A to Z", items }];
  // Suppressed VISUALLY, never removed: each clinic name is an `h3`, so
  // dropping the `h2` on a single-band list would take the reader from the
  // shell's `h1` straight to `h3` and put every clinic a level out of reach of
  // heading navigation. Same rule, same reason, as the claims ledger.
  const showHeadings = located && groups.length > 1;
  const canShowMore = items.length < total && items.length < MAX_ROWS;

  return (
    <div className="space-y-3">
      <ClinicFinder
        facets={facets}
        typeKey={typeKey}
        onTypeKey={(key) => {
          setTypeKey(key);
          setArea(ALL_AREAS);
          setShown(PAGE_SIZE);
        }}
        areas={data?.filters.areas ?? []}
        area={area}
        onArea={(value) => {
          setArea(value);
          setShown(PAGE_SIZE);
        }}
        search={search}
        onSearch={(value) => {
          setSearch(value);
          setShown(PAGE_SIZE);
        }}
        origin={origin}
        status={status}
        onGps={locateByGps}
        onPostal={(code) => void locateByPostal(code)}
        onClearOrigin={() => {
          setOrigin(null);
          setStatus({ kind: "idle" });
          setShown(PAGE_SIZE);
        }}
      />

      <p aria-live="polite" className="px-1 text-row text-label">
        {total} clinic{total === 1 ? "" : "s"}
        {located ? " · nearest first" : " · A to Z"}
        {query.isFetching && (
          <Loader2 className="ml-2 inline size-3 animate-spin align-middle" aria-hidden />
        )}
      </p>

      {items.length === 0 ? (
        <Mount label="Nothing matches">
          <p className="text-row text-label">
            {filtersActive
              ? "Try another clinic type or area, or clear what you typed."
              : "No clinics have been published for your policy yet."}
          </p>
        </Mount>
      ) : (
        groups.map((group) => (
          // `leaf-rise` on the SECTION, not the pane: the stagger is a
          // `:nth-of-type` rule over siblings.
          <section key={group.key} className="leaf-rise space-y-1.5">
            <h2 className={showHeadings ? "leaf-label px-1" : "sr-only"}>
              {group.label}
            </h2>
            <ul
              className={cn(
                glassSurface,
                "divide-y divide-hairline/75 rounded-tile p-1.5 sm:p-2",
              )}
            >
              {group.items.map((clinic) => (
                <ClinicRow key={clinic.id} clinic={clinic} clock={clock} />
              ))}
            </ul>
          </section>
        ))
      )}

      {/* Grows in place against a rising `limit` — both query hooks keep the
          previous data, so nothing flashes and focus stays on the button the
          member just pressed. Paging replaced the list under them and left
          them at the bottom of a page that no longer existed. */}
      {canShowMore && (
        <div className="flex justify-center pt-1">
          <button
            type="button"
            onClick={() => setShown((n) => Math.min(n + PAGE_SIZE, MAX_ROWS))}
            disabled={query.isFetching}
            className={actionClass("neutral", { className: "disabled:opacity-60" })}
          >
            {query.isFetching && <Loader2 className="size-4 animate-spin" aria-hidden />}
            Show {Math.min(PAGE_SIZE, total - items.length)} more
          </button>
        </div>
      )}

      {items.length >= MAX_ROWS && total > items.length && (
        <p className="px-1 text-center text-row text-label">
          Showing the first {items.length} of {total}. Narrow by area or name to
          see the rest.
        </p>
      )}
    </div>
  );
}
