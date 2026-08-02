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
 * **The list grows a PAGE at a time, appended client-side.** It used to grow by
 * raising `limit` against a fixed `offset`, which re-requested, re-ranked and
 * re-transferred every row already on screen on every press, and left a cache
 * entry per size behind it. What the member sees is unchanged — one list, one
 * "Show more" — because grow-in-place is the right reading of a page opened to
 * answer "where do I walk?"; only the traffic under it is.
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
import { singaporeNow, type ClinicClock } from "@/lib/clinicHours";
import { readableCase } from "@/lib/clinicText";
import { formatError, isNotFoundError } from "@/lib/errors";
import { geocodeSingapore } from "@/lib/geocode";
import { useDebouncedValue } from "@/lib/use-debounced-value";

/** One page, and the ONLY `limit` this page ever sends. The server validates
 * `limit` against `app/core/pagination.py::MAX_LIMIT` — it does not clamp, so a
 * request over the bound 422s — and a fixed page an order of magnitude below it
 * is what makes that unreachable by construction. A growing `limit` was not:
 * it also refetched and re-sorted every row already on screen, and minted a
 * fresh cache entry per click.
 *
 * `offset` is unbounded server-side, so paging is free to walk. */
const PAGE_SIZE = 10;
/** The client's own browse ceiling — how far down an unranked list it is worth
 * walking ten at a time — NOT a mirror of any server limit. Past it the page
 * says so and asks for a narrower question. */
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

/** An answered origin: the chip's wording plus the point the server ranks from. */
interface Origin extends OriginState {
  lat: number;
  lng: number;
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

/** One pane per distance band, hairline-divided rows inside it. */
function ClinicGroups({
  groups,
  showHeadings,
  clock,
}: {
  groups: Group[];
  showHeadings: boolean;
  clock: ClinicClock;
}) {
  return (
    <>
      {groups.map((group) => (
        // `leaf-rise` on the SECTION, not the pane: the stagger is a
        // `:nth-of-type` rule over siblings.
        <section key={group.key} className="leaf-rise space-y-1.5">
          <h2 className={showHeadings ? "leaf-label px-1" : "sr-only"}>{group.label}</h2>
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
      ))}
    </>
  );
}

/** Everything behind "Where you are": the point, how the member gave it, and
 * the two ways of asking for it. `onChange` fires whenever the answer moves, so
 * the caller can restart its paging — the ranking under it is different now. */
function useOrigin(onChange: () => void) {
  const [origin, setOrigin] = useState<Origin | null>(null);
  const [status, setStatus] = useState<OriginStatus>({ kind: "idle" });
  // Guards a stale lookup from overwriting a newer one — the origin box fires
  // on its own as soon as six digits are present, so two can be in flight, and
  // clearing must land in front of both.
  const lookupSeq = useRef(0);

  const settle = (next: Origin) => {
    setOrigin(next);
    setStatus({ kind: "idle" });
    onChange();
  };

  const byGps = () => {
    if (!("geolocation" in navigator)) {
      setStatus({
        kind: "error",
        message:
          "Your browser doesn't support location — type a postal code, building or street name instead.",
      });
      return;
    }
    const seq = ++lookupSeq.current;
    setStatus({ kind: "busy" });
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        if (seq !== lookupSeq.current) return;
        settle({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          label: "Your current location",
          postal: null,
        });
      },
      (err) => {
        if (seq !== lookupSeq.current) return;
        setStatus({
          kind: "error",
          message:
            (err.code === err.PERMISSION_DENIED
              ? "Location access was denied"
              : "We couldn't work out where you are") +
            " — type a postal code, building or street name instead.",
        });
      },
      { enableHighAccuracy: true, timeout: 10_000, maximumAge: 60_000 },
    );
  };

  /** One lookup for both halves of the box. OneMap resolves a postal code, a
   * building name and a street on the same call, so the only thing `postal`
   * changes is the wording: a code is quoted back in the chip and named in the
   * failure, an address is not. */
  const byPlace = async (place: string, postal: string | null) => {
    const seq = ++lookupSeq.current;
    setStatus({ kind: "busy" });
    try {
      const point = await geocodeSingapore(place);
      if (seq !== lookupSeq.current) return;
      if (point) {
        // OneMap answers in supplier capitals and repeats the code it was
        // given ("618 YISHUN RING ROAD SINGAPORE 760618"), which the chip is
        // already printing in bold beside it.
        settle({
          ...point,
          postal,
          label: readableCase(point.label).replace(/\s*Singapore\s+\d{6}\s*$/i, ""),
        });
      } else {
        setStatus({
          kind: "error",
          message:
            (postal
              ? "That postal code didn't resolve — check it, or try a building or street name"
              : "We couldn't find that — try a postal code, or another building or street name") +
            ", or use my location instead. (For Johor Bahru clinics, use your device location.)",
        });
      }
    } catch (error) {
      if (seq !== lookupSeq.current) return;
      setStatus({ kind: "error", message: formatError(error) });
    }
  };

  const clear = () => {
    lookupSeq.current += 1;
    setOrigin(null);
    setStatus({ kind: "idle" });
    onChange();
  };

  return { origin, status, byGps, byPlace, clear };
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
  // The page in flight, and everything already read. "Show more" appends one
  // fixed page to `loaded` instead of re-requesting the whole list at a larger
  // size — see PAGE_SIZE.
  const [offset, setOffset] = useState(0);
  const [loaded, setLoaded] = useState<Clinic[]>([]);

  /** Any change to the question restarts the walk — a page 3 of the previous
   * answer is not page 3 of this one. */
  const restart = () => {
    setOffset(0);
    setLoaded([]);
  };

  const { origin, status, byGps, byPlace, clear } = useOrigin(restart);
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
    offset,
    limit: PAGE_SIZE,
  });

  if (query.isLoading) return <LeafSkeleton label="Loading clinics" />;
  if (query.isError && !isNotFoundError(query.error)) {
    return <PortalErrorState onRetry={() => void query.refetch()} />;
  }

  const data = query.data;
  const facets = data?.filters.clinic_types ?? [];
  const total = data?.total ?? 0;
  const located = data?.located ?? false;
  const filtersActive = typeKey !== "all" || area !== ALL_AREAS || q !== "";

  // Both hooks keep the previous answer while a new one is in flight, so
  // `data` may still be the page BEFORE this one — appending it would repeat
  // rows already on screen (or, after a filter change, splice a stale page 3
  // onto an empty list). The response states its own offset; only a page that
  // answers the request we are on may be appended.
  const settled = data?.offset === offset;
  const seen = new Set(loaded.map((c) => c.id));
  const items = settled
    ? [...loaded, ...data.items.filter((c) => !seen.has(c.id))]
    : loaded;
  const loading = query.isFetching || !settled;

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
          restart();
        }}
        areas={data?.filters.areas ?? []}
        area={area}
        onArea={(value) => {
          setArea(value);
          restart();
        }}
        search={search}
        onSearch={(value) => {
          setSearch(value);
          restart();
        }}
        origin={origin}
        status={status}
        onGps={byGps}
        onLocate={(place, postal) => void byPlace(place, postal)}
        onClearOrigin={clear}
      />

      <p aria-live="polite" className="px-1 text-row text-label">
        {total} clinic{total === 1 ? "" : "s"}
        {located ? " · nearest first" : " · A to Z"}
        {query.isFetching && (
          <Loader2 className="ml-2 inline size-3 animate-spin align-middle" aria-hidden />
        )}
      </p>

      {items.length === 0 ? (
        // "Nothing matches" is an ANSWER, so it may not be shown while the
        // answer is still being fetched — a filter change empties the list for
        // as long as the request takes, and reading a verdict there would be
        // wrong about half the time it appeared.
        loading ? (
          <LeafSkeleton label="Loading clinics" />
        ) : (
          <Mount label="Nothing matches">
            <p className="text-row text-label">
              {filtersActive
                ? "Try another clinic type or area, or clear what you typed."
                : "No clinics have been published for your policy yet."}
            </p>
          </Mount>
        )
      ) : (
        <ClinicGroups groups={groups} showHeadings={showHeadings} clock={clock} />
      )}

      {/* ONE growing list, never pagination: the next page is appended under
          the rows already read, so nothing moves and focus stays on the button
          the member just pressed. Paging replaced the list under them and left
          them at the bottom of a page that no longer existed.
          Disabled while a page is in flight — a second press would advance the
          offset past the page still being appended and skip ten clinics. */}
      {canShowMore && (
        <div className="flex justify-center pt-1">
          <button
            type="button"
            onClick={() => {
              setLoaded(items);
              // The SERVER's position, not `items.length` — a row dropped by the
              // duplicate guard must not pull the next request back over one
              // already read.
              setOffset(offset + PAGE_SIZE);
            }}
            disabled={loading}
            className={actionClass("neutral", { className: "disabled:opacity-60" })}
          >
            {loading && <Loader2 className="size-4 animate-spin" aria-hidden />}
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
