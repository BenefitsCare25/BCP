import type { Query } from "@tanstack/react-query";

interface PlaceholderScope {
  clientId: string | null;
  policyYearId: string | null;
}

function scopeOf(query: Pick<Query, "meta"> | undefined): PlaceholderScope | null {
  const scope = query?.meta?.placeholderScope;
  return scope && typeof scope === "object" ? (scope as PlaceholderScope) : null;
}

/** Query options that keep the previous result on screen while the next one
 *  loads — only when it was fetched for the same company and benefit year.
 *
 *  A bare `placeholderData: (prev) => prev` also bridges a company or year
 *  switch, so the previous company's rows sat under the new company's name
 *  until the fetch landed. The scope rides in the query's `meta` because the
 *  previous query is all TanStack hands the placeholder function. */
export function keepPreviousInScope(
  clientId: string | null,
  policyYearId: string | null | undefined,
) {
  const scope: PlaceholderScope = { clientId, policyYearId: policyYearId ?? null };
  return {
    meta: { placeholderScope: scope },
    placeholderData: <T>(
      previous: T | undefined,
      previousQuery: Pick<Query, "meta"> | undefined,
    ): T | undefined => {
      const prior = scopeOf(previousQuery);
      return prior?.clientId === scope.clientId &&
        prior.policyYearId === scope.policyYearId
        ? previous
        : undefined;
    },
  };
}
