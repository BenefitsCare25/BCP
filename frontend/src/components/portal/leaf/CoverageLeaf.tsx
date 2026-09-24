import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { ArrowLeft, ArrowRight, ChevronDown, MapPin } from "lucide-react";
import type { BenefitStatement, CoverageLine, DependantSummary } from "@/types";
import { actionClass } from "./Action";
import { FlexMount } from "./FlexMount";
import { Mount, MountRule, glassHover, glassSurface } from "./Mount";
import { ScheduleLeaf } from "./ScheduleLeaf";
import { buildCareRoutes, careFacts, type CareRoute } from "./careRoutes";
import { productShortLabel } from "./glossary";
import { isEmployeeLine } from "../memberVisibility";

/** What the member is looking at. Lives in the URL on the live portal (`?p=`,
 * `?who=`) so Back, refresh and a shared link all land on the same view. */
export type CoverageSelection = { routeKey: string; personId: string | null };

function dependantName(person: DependantSummary): string {
  return person.name ?? person.relationship ?? "Family member";
}

function coveredPeople(lines: CoverageLine[]): DependantSummary[] {
  const people = new Map<string, DependantSummary>();
  for (const line of lines) {
    if (!line.covers_dependants) continue;
    for (const person of line.covered_dependants) people.set(person.id, person);
  }
  return [...people.values()];
}

function RouteCard({ route, onClick }: { route: CareRoute; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
      className={`${glassSurface} ${glassHover} leaf-focus leaf-rise flex min-h-32 w-full flex-col items-start justify-between gap-3 rounded-tile p-4 text-left sm:p-5`}>
      <span>
        <span className="block text-md font-semibold text-record">{route.title}</span>
        <span className="mt-1 block text-row text-label">{route.description}</span>
      </span>
      <span className="flex items-center gap-1.5 text-row font-semibold text-action-ink">
        View cover <ArrowRight className="size-4" aria-hidden />
      </span>
    </button>
  );
}

function PlanDetail({ line, routeKey, person }: {
  line: CoverageLine;
  routeKey: string;
  person: DependantSummary | null;
}) {
  const facts = careFacts(line, routeKey);
  const code = line.product_code.trim().toUpperCase();
  const additionalMedical = code === "GMM" || code === "GMM2";
  const label = additionalMedical
    ? "Additional major medical cover"
    : productShortLabel(line.product_code, line.product_name);

  return (
    <Mount as="article" label={label} gloss={line.plan_code ? `Plan ${line.plan_code}` : undefined}>
      <p className="text-row text-label">
        Covered person: <span className="font-medium text-record">{person ? dependantName(person) : "You"}</span>
      </p>
      {additionalMedical && (
        <p className="text-row text-label">This plan has its own conditions and limits. Check them alongside your hospital plan.</p>
      )}
      {facts.length > 0 ? (
        <>
          <MountRule />
          <dl className="divide-y divide-hairline/75">
            {facts.map((fact) => (
              <div key={fact.label} className="grid gap-1 py-3 sm:grid-cols-[minmax(0,12rem)_1fr] sm:gap-4">
                <dt className="text-row text-label">{fact.label}</dt>
                <dd className="text-row font-medium text-record">
                  {fact.value}
                  {fact.note && <span className="mt-1 block font-normal text-label">{fact.note}</span>}
                </dd>
              </div>
            ))}
          </dl>
        </>
      ) : (
        <p className="text-row text-label">
          Key amounts and conditions aren't recorded in a form we can summarise. Read the plan details below or ask your HR team before arranging care.
        </p>
      )}
      <MountRule />
      <details className="group">
        <summary className="leaf-focus flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 text-row font-semibold text-record [&::-webkit-details-marker]:hidden">
          Full benefit schedule
          <ChevronDown className="size-4 shrink-0 text-label transition-transform group-open:rotate-180" aria-hidden />
        </summary>
        <div className="pt-2"><ScheduleLeaf schedule={line.benefit_schedule} allRows /></div>
      </details>
    </Mount>
  );
}

/** Member coverage is organised around a care decision, then the matched plan. */
export function CoverageLeaf({ data, selection, onSelectionChange, company }: {
  data: BenefitStatement;
  /** Controlled by the live portal's URL; the broker preview holds its own. */
  selection?: CoverageSelection;
  onSelectionChange?: (next: CoverageSelection) => void;
  /** Present in the live portal; preview keeps navigation inside its frame. */
  company?: string;
}) {
  const [local, setLocal] = useState<CoverageSelection>({ routeKey: "", personId: null });
  const current = selection ?? local;
  const people = useMemo(() => coveredPeople(data.coverage), [data.coverage]);
  // An id that no longer resolves (dependant removed, stale link) falls back
  // to the member; "Me" and "Covered person: You" then state that honestly.
  const person = people.find((candidate) => candidate.id === current.personId) ?? null;
  const lines = useMemo(() =>
    person
      ? data.coverage.filter((line) =>
          line.covers_dependants && line.covered_dependants.some((candidate) => candidate.id === person.id),
        )
      : data.coverage.filter(isEmployeeLine),
    [data.coverage, person],
  );
  const routes = useMemo(() => buildCareRoutes(lines), [lines]);
  const selected = routes.find((route) =>
    route.key === current.routeKey || route.lines.some((line) => line.product_code === current.routeKey),
  ) ?? null;
  const change = (next: CoverageSelection) => {
    if (onSelectionChange) onSelectionChange(next);
    else setLocal(next);
  };
  const select = (routeKey: string) => change({ routeKey, personId: person?.id ?? null });
  const choosePerson = (personId: string | null) => change({ routeKey: "", personId });

  if (routes.length === 0 && !data.flex && people.length === 0) {
    return (
      <Mount label="No care benefits to show">
        <p className="text-row text-label">We don't have any care benefits recorded against your name for this period. Your HR team can check your record.</p>
      </Mount>
    );
  }

  return (
    <div className="space-y-4">
      {people.length > 0 && (
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Choose covered person">
          <span className="mr-1 text-row text-label">Cover for</span>
          <button type="button" onClick={() => choosePerson(null)} aria-pressed={!person}
            className={`${actionClass("neutral")} ${!person ? "bg-shade" : ""}`}>
            Me
          </button>
          {people.map((candidate) => (
            <button key={candidate.id} type="button" onClick={() => choosePerson(candidate.id)}
              aria-pressed={person?.id === candidate.id}
              className={`${actionClass("neutral")} ${person?.id === candidate.id ? "bg-shade" : ""}`}>
              {dependantName(candidate)}
            </button>
          ))}
        </div>
      )}

      {selected ? (
        <>
          <button type="button" onClick={() => select("")}
            className="leaf-focus inline-flex min-h-11 items-center gap-2 text-row font-semibold text-action-ink">
            <ArrowLeft className="size-4" aria-hidden /> All care options
          </button>
          <div>
            <h2 className="text-2xl font-semibold tracking-title text-record">{selected.title}</h2>
            <p className="mt-1 text-row text-label">{selected.description}</p>
          </div>
          <div className="grid gap-4">
            {selected.lines.map((line, index) => (
              <PlanDetail key={`${line.product_code}-${line.plan_code ?? ""}-${index}`} line={line} routeKey={selected.key} person={person} />
            ))}
          </div>
          {company && ["gp", "specialist", "dental"].includes(selected.key) && (
            <Link to="/portal/$company/clinics" params={{ company }} className={actionClass("quiet", { block: "phone" })}>
              <MapPin className="size-4" aria-hidden /> Find a clinic
            </Link>
          )}
        </>
      ) : (
        <>
          <div>
            <h2 className="text-2xl font-semibold tracking-title text-record">
              {routes.some((route) => route.section === "care") ? "What care do you need?" : "Your cover"}
            </h2>
            <p className="mt-1 text-row text-label">Choose a topic to see the cover that applies to {person ? dependantName(person) : "you"}.</p>
          </div>
          {routes.filter((route) => route.section === "care").length > 0 && (
            <div className="grid gap-3 sm:grid-cols-2 sm:gap-4">
              {routes.filter((route) => route.section === "care").map((route) => (
                <RouteCard key={route.key} route={route} onClick={() => select(route.key)} />
              ))}
            </div>
          )}
          {routes.filter((route) => route.section === "other").length > 0 && (
            <section className="space-y-3" aria-label="Other cover">
              <h3 className="text-md font-semibold text-record">Other cover</h3>
              <div className="grid gap-3 sm:grid-cols-2 sm:gap-4">
                {routes.filter((route) => route.section === "other").map((route) => (
                  <RouteCard key={route.key} route={route} onClick={() => select(route.key)} />
                ))}
              </div>
            </section>
          )}
          {routes.length === 0 && (
            <Mount label="No care routes for this person">
              <p className="text-row text-label">No care benefits are recorded for this person in the current benefit year.</p>
            </Mount>
          )}
          {data.flex && !person && <FlexMount flex={data.flex} />}
        </>
      )}
    </div>
  );
}
