/** The company alias the current portal page is scoped to.
 *
 * Every portal route is `/portal/$company/…`, so each `Link`/`navigate` inside
 * the portal has to supply that param. This is the one place it is read.
 *
 * `strict: false` because the portal's components are mounted from a dozen
 * different routes (and from the broker's employee-view preview, which is not a
 * portal route at all) — pinning a `from` would make each of them declare which
 * route it believes it is under, and be wrong the moment one is reused.
 *
 * The fallback is not defensive padding. `MemberCard`, `ClaimMount` and the
 * coverage leaves are rendered by `routes/operations/employee-view.tsx` on the
 * BROKER surface, where there is no `$company` param at all; without it those
 * shared components would build links with `undefined` in the path. It resolves
 * through `currentPortalTenantSlug()` for the same reason the fetch layer does.
 */
import { useParams } from "@tanstack/react-router";
import { currentPortalTenantSlug } from "@/lib/tenant";

export function useCompany(): string {
  const params = useParams({ strict: false }) as { company?: string };
  return params.company ?? currentPortalTenantSlug();
}
