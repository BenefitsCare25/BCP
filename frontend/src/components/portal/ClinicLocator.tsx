/** Clinic locator — shared between the member portal (/portal/clinics) and
 * the broker employee-view preview (PortalFrame). The data hook is injected
 * so each surface fetches through its own auth client; both endpoints return
 * the identical ClinicSearch shape.
 *
 * Core flow: set an origin (GPS or a typed Singapore postal code/address,
 * geocoded via OneMap) → the list becomes the nearest clinics first, 10 per
 * page, with distances on every card. */
import { useMemo, useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import {
  Clock,
  ExternalLink,
  Loader2,
  LocateFixed,
  MapPin,
  Phone,
  Search,
  Stethoscope,
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PaginationControls } from "@/components/ui/pagination-controls";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { InfoHint } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";
import { formatError, isNotFoundError } from "@/lib/errors";
import { geocodeSingapore, type GeocodedPoint } from "@/lib/geocode";
import { useDebouncedValue } from "@/lib/use-debounced-value";

// "Top 10 nearest" is the product requirement — one page = the top 10.
const PAGE_SIZE = 10;
const ALL_AREAS = "__all__";
// Mirrors the backend Query(max_length=128) bound so a long paste can't 422.
const MAX_QUERY_LENGTH = 128;

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

function ClinicCard({ clinic, rank }: { clinic: Clinic; rank: number | null }) {
  const [showHours, setShowHours] = useState(false);
  const phoneHref = clinic.phone ? telHref(clinic.phone) : null;
  const hours = clinic.hours ?? {};
  const hasHours = HOURS_LABELS.some(([key]) => hours[key]);

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">
            {rank !== null && (
              <span className="mr-1.5 text-muted-foreground">{rank}.</span>
            )}
            {clinic.name}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <Badge variant="outline">{clinic.type_label}</Badge>
            {clinic.area && <Badge variant="default">{clinic.area}</Badge>}
            {clinic.specialty && (
              <Badge variant="default">{clinic.specialty}</Badge>
            )}
          </div>
        </div>
        {clinic.distance_km !== null && (
          <Badge variant="outline" className="shrink-0">
            {formatDistance(clinic.distance_km)}
          </Badge>
        )}
      </div>

      <div className="mt-3 space-y-1.5 text-sm text-muted-foreground">
        {clinic.address && (
          <p className="flex items-start gap-2">
            <MapPin className="mt-0.5 size-4 shrink-0" />
            <span>{clinic.address}</span>
          </p>
        )}
        {clinic.doctor && (
          <p className="flex items-start gap-2">
            <Stethoscope className="mt-0.5 size-4 shrink-0" />
            <span>{clinic.doctor}</span>
          </p>
        )}
        {clinic.phone && (
          <p className="flex items-start gap-2">
            <Phone className="mt-0.5 size-4 shrink-0" />
            {phoneHref ? (
              <a
                href={phoneHref}
                className="text-primary underline-offset-2 hover:underline"
              >
                {clinic.phone}
              </a>
            ) : (
              <span>{clinic.phone}</span>
            )}
          </p>
        )}
      </div>

      {showHours && hasHours && (
        <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 rounded-md bg-muted/60 p-3 text-xs sm:grid-cols-2">
          {HOURS_LABELS.map(([key, label]) =>
            hours[key] ? (
              <div key={key} className="flex justify-between gap-3">
                <dt className="shrink-0 font-medium text-foreground">{label}</dt>
                <dd className="text-right text-muted-foreground">
                  {hours[key]}
                </dd>
              </div>
            ) : null,
          )}
        </dl>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {hasHours && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowHours((v) => !v)}
          >
            <Clock className="size-4" />
            <span className="ml-1">
              {showHours ? "Hide hours" : "Opening hours"}
            </span>
          </Button>
        )}
        {clinic.google_map_url && (
          <Button variant="outline" size="sm" asChild>
            <a
              href={clinic.google_map_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <ExternalLink className="size-4" />
              <span className="ml-1">Open in Google Maps</span>
            </a>
          </Button>
        )}
      </div>
    </div>
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
  const busy = status.kind === "busy";
  const submit = () => {
    if (query.trim()) {
      onSearch(query.trim());
      setQuery("");
    }
  };

  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <p className="text-xs font-medium text-foreground">Your location</p>
      <div className="mt-2 flex flex-col gap-2 sm:flex-row">
        <Button
          variant="outline"
          onClick={onGps}
          disabled={busy}
          className="shrink-0"
        >
          {busy ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <LocateFixed className="size-4" />
          )}
          <span className="ml-1">Use my location</span>
        </Button>
        <div className="flex flex-1 gap-2">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
            maxLength={MAX_QUERY_LENGTH}
            placeholder="or enter a postal code / address (e.g. 760618)"
            aria-label="Enter a postal code or address"
            disabled={busy}
          />
          <Button
            variant="secondary"
            onClick={submit}
            disabled={busy || query.trim() === ""}
            className="shrink-0"
          >
            Set
          </Button>
        </div>
      </div>
      {origin && (
        <div className="mt-2 flex items-center gap-2">
          <Badge variant="outline" className="max-w-full">
            <MapPin className="mr-1 size-3 shrink-0" />
            <span className="truncate">Near {origin.label}</span>
          </Badge>
          <button
            type="button"
            onClick={onClear}
            className="inline-flex items-center gap-0.5 text-xs text-muted-foreground hover:text-foreground"
            aria-label="Clear location"
          >
            <X className="size-3" /> Clear
          </button>
        </div>
      )}
      {status.kind === "error" && (
        <p className="mt-2 text-xs text-warn">{status.message}</p>
      )}
    </div>
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
  const chips = [
    { key: "all", label: "All", count: null as number | null },
    ...facets.map((f) => ({
      key: `${f.country}:${f.clinic_type}`,
      label: f.label,
      count: f.count as number | null,
    })),
  ];
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <div
          className="flex flex-wrap gap-1.5"
          role="group"
          aria-label="Clinic type"
        >
          {chips.map((chip) => (
            <button
              key={chip.key}
              type="button"
              onClick={() => onTypeKey(chip.key)}
              className={cn(
                "inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                typeKey === chip.key
                  ? "border-primary bg-accent text-accent-foreground"
                  : "border-border text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {chip.label}
              {chip.count !== null && (
                <span className="tabular-nums opacity-70">{chip.count}</span>
              )}
            </button>
          ))}
        </div>
        {areas.length > 0 && (
          <Select value={area} onValueChange={onArea}>
            <SelectTrigger
              className="h-8 w-44 text-xs"
              aria-label="Filter by area"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="max-h-72">
              <SelectItem value={ALL_AREAS}>All areas</SelectItem>
              {areas.map((a) => (
                <SelectItem key={a} value={a}>
                  {a}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <InfoHint side="left">
          Chips filter by consultation type (GP, dental, TCM, specialist). Pick
          a type to narrow the area list; the box below searches clinic name,
          address or postal code within the current filter.
        </InfoHint>
      </div>
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          maxLength={MAX_QUERY_LENGTH}
          placeholder="Filter by clinic name, address or postal code"
          className="pl-9"
          aria-label="Filter clinics"
        />
      </div>
    </>
  );
}

function EmptyResults({ filtersActive }: { filtersActive: boolean }) {
  return (
    <div className="rounded-lg border border-border bg-card p-8 text-center">
      <Search className="mx-auto size-6 text-muted-foreground" />
      <p className="mt-2 text-sm font-medium text-foreground">
        No clinics match
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        {filtersActive
          ? "Try a different clinic type, area or search term."
          : "No clinics have been published for your policy yet."}
      </p>
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

  if (query.isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }
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

  if (query.isError || (!query.isError && facets.length === 0)) {
    return (
      <div className="rounded-lg border border-border bg-card p-8 text-center">
        <MapPin className="mx-auto size-6 text-muted-foreground" />
        <p className="mt-2 text-sm font-medium text-foreground">
          No panel clinics available yet
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Your benefits provider hasn't published panel clinic lists for your
          policy — check back later or contact your HR / broker.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
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

      <p className="text-xs text-muted-foreground">
        {located && rankBase === 0
          ? `Nearest ${Math.min(PAGE_SIZE, total)} of ${total} clinic${total === 1 ? "" : "s"} near ${origin?.label ?? "you"}`
          : located
            ? `${total} clinics — nearest first from ${origin?.label ?? "you"}`
            : `${total} clinic${total === 1 ? "" : "s"} — sorted by name`}
        {query.isFetching && (
          <Loader2 className="ml-2 inline size-3 animate-spin align-middle" />
        )}
      </p>

      {items.length === 0 ? (
        <EmptyResults filtersActive={filtersActive} />
      ) : (
        <div className="space-y-3">
          {items.map((clinic, idx) => (
            <ClinicCard
              key={clinic.id}
              clinic={clinic}
              rank={
                located && clinic.distance_km !== null
                  ? rankBase + idx + 1
                  : null
              }
            />
          ))}
        </div>
      )}

      <PaginationControls
        page={page}
        pages={Math.max(1, Math.ceil(total / PAGE_SIZE))}
        onPageChange={setPage}
      />
    </div>
  );
}
