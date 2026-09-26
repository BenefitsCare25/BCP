import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { ArrowLeft, ChevronDown, MapPin } from "lucide-react";
import type { BenefitStatement, CoverageLine, DependantSummary } from "@/types";
import { actionClass } from "./Action";
import { FlexMount } from "./FlexMount";
import { Mount, MountRule } from "./Mount";
import { ScheduleLeaf } from "./ScheduleLeaf";
import { buildCareRoutes } from "./careRoutes";
import { careFacts } from "./careFacts";
import { careArt, careTone } from "./careTone";
import { BenefitSheetButton } from "../home/BenefitSheet";
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

function PlanDetail({ line, routeKey, person }: {
  line: CoverageLine;
  routeKey: string;
  person: DependantSummary | null;
}) {
  const code = line.product_code.trim().toUpperCase();
  const additionalMedical = code === "GMM" || code === "GMM2";
  const label = additionalMedical
    ? "Extra cover after your hospital plan"
    : productShortLabel(line.product_code, line.product_name);

  // The broker hasn't confirmed this plan yet, so nothing it says is
  // published. The cover still exists, so the member is told it's coming
  // rather than shown nothing.
  if (line.published === false) {
    return (
      <Mount as="article" label={label} gloss={line.plan_code ? `Plan ${line.plan_code}` : undefined}>
        <p className="text-row text-label">
          Your plan details are being checked and will appear here once they're confirmed.
          Your HR team can help in the meantime.
        </p>
      </Mount>
    );
  }

  // Voluntary cover on the slip is an offer until taken up: say so plainly
  // rather than presenting it as cover the member can claim against.
  if (line.enrolment === "eligible") {
    return (
      <Mount as="article" label={label} gloss={line.plan_code ? `Plan ${line.plan_code}` : undefined}>
        <p className="text-row text-label">
          You're eligible for this voluntary cover but not enrolled, so it can't be claimed yet.
          Your HR team can tell you how to join.
        </p>
      </Mount>
    );
  }

  const facts = careFacts(line, routeKey);
  return (
    <Mount as="article" label={label} gloss={line.plan_code ? `Plan ${line.plan_code}` : undefined}>
      <p className="text-row text-label">
        Covered person: <span className="font-medium text-record">{person ? dependantName(person) : "You"}</span>
      </p>
      {additionalMedical && (
        <p className="text-row text-label">Pays for bigger hospital bills once your hospital plan's limits are used. It has its own conditions, listed below.</p>
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
          {routeKey === "dental" ? "Treatment price list and full details" : "Full benefit schedule"}
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
          <div className={`tone-${careTone(selected.key)} relative flex min-h-36 items-end overflow-hidden rounded-[28px] bg-[var(--tone-wash)] p-6 pr-40 sm:min-h-44 sm:p-8 sm:pr-56`}>
            <div>
              <span className="clay-tag">{productShortLabel(selected.lines[0].product_code, selected.lines[0].product_name)}</span>
              <h2 className="mt-4 text-3xl font-bold tracking-title text-record sm:text-4xl">{selected.title}</h2>
              <p className="mt-1 text-row text-[var(--tone-ink)]">{selected.description}</p>
            </div>
            {careArt(selected.key) && (
              <img src={careArt(selected.key)!} alt="" className="pointer-events-none absolute -bottom-2 right-2 size-36 object-contain sm:right-6 sm:size-48" />
            )}
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
          {routes.filter((route) => route.section === "care").length > 0 && (
            <div className="clay-sheets">
              {routes.filter((route) => route.section === "care").map((route) => (
                <BenefitSheetButton key={route.key} route={route} onClick={() => select(route.key)} />
              ))}
            </div>
          )}
          {routes.filter((route) => route.section === "other").length > 0 && (
            <section className="space-y-3" aria-label="Other cover">
              <h3 className="clay-subhead !m-0">Also covered</h3>
              <div className="clay-sheets clay-sheets-other">
                {routes.filter((route) => route.section === "other").map((route) => (
                  <BenefitSheetButton key={route.key} route={route} onClick={() => select(route.key)} />
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
