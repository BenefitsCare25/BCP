/** Find a clinic — the one portal screen a member opens while already standing
 * somewhere, deciding where to walk.
 *
 * That framing decides the layout. The origin comes first, because a list of
 * 836 clinics sorted alphabetically answers nothing; once an origin is set the
 * list is the ten nearest, ranked, with the distance set as a figure beside
 * each name. And on every clinic the phone number is a full-height action
 * rather than a line of text — at this point in the task, calling ahead IS the
 * task, and it was previously a 66×20px link.
 *
 * Shared between `/portal/clinics` and the broker employee-view preview
 * (PortalFrame); both render inside `.leaf`. The data hook is injected so each
 * surface fetches through its own auth client, and both endpoints return the
 * identical ClinicSearch shape — which is what stops the preview drifting from
 * what the member actually sees.
 *
 * Native `<select>` rather than the shared Radix one on purpose: Radix portals
 * its listbox to `document.body`, i.e. OUTSIDE the `.leaf` subtree, so it would
 * render in the broker's tokens and type on the member's screen. A native
 * select also gets the platform's own picker on a phone. */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import {
  ChevronDown,
  Clock,
  ExternalLink,
  Loader2,
  LocateFixed,
  Phone,
  Search,
  X,
} from "lucide-react";
import type {
  Clinic,
  ClinicSearch,
  ClinicSearchParams,
  ClinicType,
  ClinicTypeFacet,
  PanelCountry,
} from "@/api/panelListings";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import { Mount, MountRule } from "@/components/portal/leaf/Mount";
import { leafControl } from "@/components/portal/leaf/Field";
import { actionClass } from "@/components/portal/leaf/Action";
import { cn } from "@/lib/cn";
import { formatError, isNotFoundError } from "@/lib/errors";
import { geocodeSingapore, type GeocodedPoint } from "@/lib/geocode";
import { useDebouncedValue } from "@/lib/use-debounced-value";

// "Top 10 nearest" is the product requirement — one page = the top 10.
const PAGE_SIZE = 10;
const ALL_AREAS = "__all__";
// Mirrors the backend Query(max_length=128) bound so a long paste can't 422.
const MAX_QUERY_LENGTH = 128;

/** Every control on this page is a leaf action — 44×44 by construction, which
 * is how the page went from 49 undersized targets to none. They are the SHARED
 * ones now: this file used to carry its own copy of the class string, alongside
 * near-identical copies in security and enrollment, and they had already
 * drifted apart.
 *
 * Two tones, split by what the control DOES. This one is for the things a
 * member came here to do — call the clinic, open directions, set an origin. */
const leafAction = actionClass("quiet", { className: "px-3" });
/** Controls that CHOOSE a view rather than do something: the type chips, the
 * opening-hours disclosure, the pager. See the tone note in leaf/Action. */
const leafPick = actionClass("neutral", { className: "px-3" });

type OriginStatus =
  | { kind: "idle" }
  | { kind: "busy" }
  | { kind: "error"; message: string };

interface Origin extends GeocodedPoint {
  source: "gps" | "search";
}

const HOURS_LABELS: [keyof NonNullable<Clinic["hours"]>, string][] = [
  ["mon_fri", "Mon–Fri"],
  ["sat", "Sat"],
  ["sun", "Sun"],
  ["public_holiday", "Public holiday"],
];

function telHref(phone: string): string | null {
  // Panel phone cells often carry remarks ("62353490 - FIRST DAY ..."); dial
  // only the leading number.
  const match = phone.match(/[\d\s+-]{6,}/);
  if (!match) return null;
  const digits = match[0].replace(/[^\d+]/g, "");
  return digits.length >= 6 ? `tel:${digits}` : null;
}

function formatDistance(km: number): string {
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(1)} km`;
}

/** Type, area and specialty, printed as one line rather than three pills. The
 * album has no pill shapes, and three badges of equal weight beside a clinic
 * name competed with the name itself. */
function clinicGloss(clinic: Clinic): string {
  return [clinic.type_label, clinic.area, clinic.specialty]
    .filter(Boolean)
    .join(" · ");
}

function ClinicLeaf({ clinic, rank }: { clinic: Clinic; rank: number | null }) {
  const [showHours, setShowHours] = useState(false);
  const phoneHref = clinic.phone ? telHref(clinic.phone) : null;
  const hours = clinic.hours ?? {};
  const hasHours = HOURS_LABELS.some(([key]) => hours[key]);

  return (
    <Mount
      as="li"
      label={
        <>
          {rank !== null && (
            <span className="mr-1.5 font-normal text-label">{rank}.</span>
          )}
          {clinic.name}
        </>
      }
      gloss={clinicGloss(clinic)}
      aside={
        clinic.distance_km !== null ? (
          <span className="whitespace-nowrap text-row font-semibold text-record">
            {formatDistance(clinic.distance_km)}
          </span>
        ) : undefined
      }
    >
      {clinic.address && (
        <p className="text-row text-label">
          {clinic.address}
        </p>
      )}
      {clinic.doctor && (
        <p className="mt-1 text-row text-label">
          {clinic.doctor}
        </p>
      )}

      {/* The phone number is the action, not a detail: a member reading this
          is deciding whether to walk over, and ringing ahead is what settles
          it. Full-width on a phone so it can be hit one-handed. */}
      {clinic.phone &&
        (phoneHref ? (
          <a
            href={phoneHref}
            className={cn(leafAction, "mt-3 w-full sm:w-auto")}
          >
            <Phone className="size-4" aria-hidden />
            Call {clinic.phone}
          </a>
        ) : (
          <p className="mt-3 flex items-start gap-2 text-row text-record">
            <Phone className="mt-0.5 size-4 shrink-0 text-label" aria-hidden />
            {clinic.phone}
          </p>
        ))}

      {(hasHours || clinic.google_map_url) && (
        <div className="mt-2 flex flex-wrap gap-2">
          {hasHours && (
            <button
              type="button"
              onClick={() => setShowHours((v) => !v)}
              aria-expanded={showHours}
              className={leafPick}
            >
              <Clock className="size-4" aria-hidden />
              Opening hours
              <ChevronDown
                className={cn("size-4 transition-transform", showHours && "rotate-180")}
                aria-hidden
              />
            </button>
          )}
          {clinic.google_map_url && (
            <a
              href={clinic.google_map_url}
              target="_blank"
              rel="noopener noreferrer"
              className={leafAction}
            >
              <ExternalLink className="size-4" aria-hidden />
              Directions
              <span className="sr-only">(opens in a new tab)</span>
            </a>
          )}
        </div>
      )}

      {showHours && hasHours && (
        <>
          <MountRule className="mt-3" />
          <dl className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
            {HOURS_LABELS.map(([key, label]) =>
              hours[key] ? (
                <div
                  key={key}
                  className="flex items-baseline justify-between gap-3 py-2"
                >
                  <dt className="shrink-0 text-row text-label">
                    {label}
                  </dt>
                  <dd className="text-right text-row text-record">
                    {hours[key]}
                  </dd>
                </div>
              ) : null,
            )}
          </dl>
        </>
      )}
    </Mount>
  );
}

function OriginPanel({
  origin,
  status,
  onGps,
  onSearch,
  onClear,
}: {
  origin: Origin | null;
  status: OriginStatus;
  onGps: () => void;
  onSearch: (query: string) => void;
  onClear: () => void;
}) {
  const [query, setQuery] = useState("");
  const originId = useId();
  const busy = status.kind === "busy";
  const submit = () => {
    if (query.trim()) {
      onSearch(query.trim());
      setQuery("");
    }
  };

  return (
    <Mount>
      <p className="leaf-label">Where you are</p>
      <div className="mt-2 space-y-2">
        <button
          type="button"
          onClick={onGps}
          disabled={busy}
          className={cn(leafAction, "w-full disabled:opacity-60")}
        >
          {busy ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <LocateFixed className="size-4" aria-hidden />
          )}
          Use my location
        </button>
        <div className="flex gap-2">
          {/* `useId`, not a literal: this component is rendered by BOTH
              `/portal/clinics` and the broker's employee-view preview, and a
              hardcoded id resolves every `htmlFor` in the document to whichever
              instance mounted first. `leaf/Field.tsx` solves it the same way. */}
          <label htmlFor={originId} className="sr-only">
            Postal code or address
          </label>
          <input
            id={originId}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
            maxLength={MAX_QUERY_LENGTH}
            inputMode="text"
            placeholder="or a postal code — 760618"
            disabled={busy}
            className={cn(leafControl, "flex-1")}
          />
          <button
            type="button"
            onClick={submit}
            disabled={busy || query.trim() === ""}
            className={cn(leafAction, "shrink-0 disabled:opacity-60")}
          >
            Set
          </button>
        </div>
      </div>
      {origin && (
        <p className="mt-2 flex items-center gap-2 text-row text-label">
          <span className="min-w-0 flex-1 truncate">Near {origin.label}</span>
          <button
            type="button"
            onClick={onClear}
            aria-label="Clear location"
            className="leaf-focus -m-3 inline-flex size-11 shrink-0 items-center justify-center text-label"
          >
            <X className="size-4" aria-hidden />
          </button>
        </p>
      )}
      {status.kind === "error" && (
        // role=alert: this arrives asynchronously, after a tap, and the member
        // is usually looking at the button they just pressed.
        <p
          role="alert"
          className="mt-2 text-row text-strike-pending"
        >
          {status.message}
        </p>
      )}
    </Mount>
  );
}

function FilterControls({
  facets,
  typeKey,
  onTypeKey,
  areas,
  area,
  onArea,
  search,
  onSearch,
}: {
  facets: ClinicTypeFacet[];
  typeKey: string;
  onTypeKey: (key: string) => void;
  areas: string[];
  area: string;
  onArea: (area: string) => void;
  search: string;
  onSearch: (value: string) => void;
}) {
  const searchId = useId();
  const areaId = useId();
  const chips = [
    { key: "all", label: "All", count: null as number | null },
    ...facets.map((f) => ({
      key: `${f.country}:${f.clinic_type}`,
      label: f.label,
      count: f.count as number | null,
    })),
  ];
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Clinic type">
        {chips.map((chip) => {
          const active = typeKey === chip.key;
          return (
            <button
              key={chip.key}
              type="button"
              onClick={() => onTypeKey(chip.key)}
              aria-pressed={active}
              className={cn(
                leafPick,
                "px-3",
                // Ink, not brand: an active chip is marked the way every other
                // current thing in this world is marked (nav, dock, tabs).
                active && "bg-shade font-semibold text-record",
              )}
            >
              {chip.label}
              {chip.count !== null && (
                <span className="font-normal text-label">{chip.count}</span>
              )}
            </button>
          );
        })}
      </div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-label"
            aria-hidden
          />
          <label htmlFor={searchId} className="sr-only">
            Filter by clinic name, address or postal code
          </label>
          <input
            id={searchId}
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            maxLength={MAX_QUERY_LENGTH}
            placeholder="Clinic name or address"
            className={cn(leafControl, "pl-9")}
          />
        </div>
        {areas.length > 0 && (
          <>
            <label htmlFor={areaId} className="sr-only">
              Filter by area
            </label>
            <select
              id={areaId}
              value={area}
              onChange={(e) => onArea(e.target.value)}
              className={cn(leafControl, "sm:w-48")}
            >
              <option value={ALL_AREAS}>All areas</option>
              {areas.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </>
        )}
      </div>
    </div>
  );
}

export function ClinicLocator({
  useClinicsQuery,
}: {
  /** Injected data source — portal or broker-preview hook. */
  useClinicsQuery: (
    params: ClinicSearchParams,
  ) => UseQueryResult<ClinicSearch, Error>;
}) {
  const [typeKey, setTypeKey] = useState<string>("all");
  const [area, setArea] = useState<string>(ALL_AREAS);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [origin, setOrigin] = useState<Origin | null>(null);
  const [originStatus, setOriginStatus] = useState<OriginStatus>({
    kind: "idle",
  });
  const q = useDebouncedValue(search.trim(), 300);
  const summaryRef = useRef<HTMLParagraphElement>(null);
  // Paging replaces the list in place, so without this the member is left at
  // the bottom of the previous page looking at results 11-20's footer. Moving
  // focus to the summary scrolls it into view AND announces the new count.
  const pagedRef = useRef(false);

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
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  });

  useEffect(() => {
    if (pagedRef.current) {
      pagedRef.current = false;
      summaryRef.current?.focus();
    }
  }, [page]);

  const setNewOrigin = (next: Origin) => {
    setOrigin(next);
    setOriginStatus({ kind: "idle" });
    setPage(0);
  };

  const locateByGps = () => {
    if (!("geolocation" in navigator)) {
      setOriginStatus({
        kind: "error",
        message:
          "Your browser doesn't support location — enter a postal code or address instead.",
      });
      return;
    }
    setOriginStatus({ kind: "busy" });
    navigator.geolocation.getCurrentPosition(
      (pos) =>
        setNewOrigin({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          label: "your current location",
          source: "gps",
        }),
      (err) =>
        setOriginStatus({
          kind: "error",
          message:
            err.code === err.PERMISSION_DENIED
              ? "Location access was denied — enter a postal code or address instead."
              : "We couldn't determine your location — enter a postal code or address instead.",
        }),
      { enableHighAccuracy: true, timeout: 10_000, maximumAge: 60_000 },
    );
  };

  const locateBySearch = async (value: string) => {
    setOriginStatus({ kind: "busy" });
    try {
      const point = await geocodeSingapore(value);
      if (point) {
        setNewOrigin({ ...point, source: "search" });
      } else {
        setOriginStatus({
          kind: "error",
          message:
            "Couldn't find that location — try a 6-digit Singapore postal code or a street/building name. (For JB clinics, use your device location.)",
        });
      }
    } catch (error) {
      setOriginStatus({ kind: "error", message: formatError(error) });
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
  // Rank against the RESPONSE's offset (not the page state) so placeholder
  // data from the previous page never renders with the next page's numbers,
  // and only rank clinics that actually have a distance.
  const rankBase = data?.offset ?? 0;
  const filtersActive = typeKey !== "all" || area !== ALL_AREAS || q !== "";
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

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

  const goToPage = (next: number) => {
    pagedRef.current = true;
    setPage(next);
  };

  return (
    <div className="space-y-3">
      <OriginPanel
        origin={origin}
        status={originStatus}
        onGps={locateByGps}
        onSearch={(value) => void locateBySearch(value)}
        onClear={() => {
          setOrigin(null);
          setPage(0);
        }}
      />

      <FilterControls
        facets={facets}
        typeKey={typeKey}
        onTypeKey={(key) => {
          setTypeKey(key);
          setArea(ALL_AREAS);
          setPage(0);
        }}
        areas={data?.filters.areas ?? []}
        area={area}
        onArea={(value) => {
          setArea(value);
          setPage(0);
        }}
        search={search}
        onSearch={(value) => {
          setSearch(value);
          setPage(0);
        }}
      />

      <p
        ref={summaryRef}
        tabIndex={-1}
        aria-live="polite"
        className="leaf-focus text-row text-label"
      >
        {located
          ? `${total} clinic${total === 1 ? "" : "s"}, nearest first`
          : `${total} clinic${total === 1 ? "" : "s"}, A to Z`}
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
        <ul className="space-y-3">
          {items.map((clinic, idx) => (
            <ClinicLeaf
              key={clinic.id}
              clinic={clinic}
              rank={
                located && clinic.distance_km !== null
                  ? rankBase + idx + 1
                  : null
              }
            />
          ))}
        </ul>
      )}

      {pages > 1 && (
        <div className="flex items-center justify-between gap-3 pt-1">
          <button
            type="button"
            onClick={() => goToPage(Math.max(0, page - 1))}
            disabled={page === 0}
            className={cn(leafPick, "disabled:opacity-40")}
          >
            Previous
          </button>
          <span className="text-row text-label">
            {page + 1} of {pages}
          </span>
          <button
            type="button"
            onClick={() => goToPage(Math.min(pages - 1, page + 1))}
            disabled={page >= pages - 1}
            className={cn(leafPick, "disabled:opacity-40")}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
