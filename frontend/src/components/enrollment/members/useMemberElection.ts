/** One member's election: the server record, the broker's on-screen edits,
 * the live flex maths and the payload those edits would send. */
import { useEffect, useMemo, useRef, useState } from "react";
import { useBenefitStatement } from "@/api/hooks";
import {
  type EnrollmentDetail,
  type EnrollmentStatus,
  type EnrollmentWindow,
  type ProductTierSet,
  useEnrollment,
  useEnrollmentOptions,
} from "@/api/enrollment";
import {
  type ProductState,
  buildElectionsPayload,
  computeFlex,
  dependantParticipationFor,
  sameElection,
  seedElectionState,
} from "@/components/enrollment/electionCore";

type Elections = Record<string, ProductState>;
type LeaveEntry = { action: string; days: string };
type Seed = { elections: Elections; leave: LeaveEntry };

function seedFrom(enr: EnrollmentDetail, tierSets: ProductTierSet[]): Seed {
  return {
    elections: seedElectionState(enr, tierSets),
    leave: { action: enr.leave?.action ?? "none", days: String(enr.leave?.days ?? 0) },
  };
}

/** Take the server's record for every product the broker has not edited since
 * the last seed, and keep the ones they have. A refetch — Save leave
 * invalidates the record, a window focus refetches it — must not silently put
 * unsaved plan, decline or dependant edits back to the saved draft. */
function mergeSeed(
  current: Elections,
  previous: Elections | null,
  next: Elections,
  tierSets: ProductTierSet[],
): Elections {
  return Object.fromEntries(
    tierSets.map((ts) => {
      const code = ts.product_code;
      const mine = current[code];
      const clean =
        !mine ||
        !previous ||
        sameElection(mine, previous[code], {
          // Compulsory cover's dependant set is the server's to resolve.
          ignoreDependantIds:
            dependantParticipationFor(ts, mine.tierKey) === "compulsory",
        });
      return [code, clean ? next[code] : mine];
    }),
  );
}

export function useMemberElection(enrollmentId: string, window: EnrollmentWindow) {
  const allowLeave = window.allow_leave;
  const allowDeps = window.allow_dependant_changes;
  const { data: enr, isLoading, isError, error, refetch } = useEnrollment(enrollmentId);
  const { data: options } = useEnrollmentOptions(enrollmentId);
  const empId = enr?.employee_id ?? null;
  // Loaded even when dependant changes are off: compulsory family cover is
  // still covered and charged to the wallet, so the balance must price it.
  const { data: statement } = useBenefitStatement(empId);
  const dependants = useMemo(() => statement?.dependants ?? [], [statement]);

  const productScope = window.product_scope;
  const tierSets = useMemo<ProductTierSet[]>(() => {
    const all = options?.products ?? [];
    const scope = productScope?.length ? new Set(productScope) : null;
    return scope ? all.filter((p) => scope.has(p.product_code)) : all;
  }, [options, productScope]);

  const [state, setState] = useState<Elections>({});
  const [leave, setLeave] = useState<LeaveEntry>({ action: "none", days: "0" });
  const seed = useRef<Seed | null>(null);

  useEffect(() => {
    if (!enr || !options) return;
    const previous = seed.current;
    const next = seedFrom(enr, tierSets);
    seed.current = next;
    setState((cur) => mergeSeed(cur, previous?.elections ?? null, next.elections, tierSets));
    setLeave((cur) =>
      !previous ||
      (cur.action === previous.leave.action && cur.days === previous.leave.days)
        ? next.leave
        : cur,
    );
  }, [enr, options, tierSets]);

  const flex = computeFlex(
    options, tierSets, state, dependants, allowDeps, leave.action, leave.days,
    options?.leave ?? null,
  );

  // `validate_leave` requires 0 days with "none", and the days field locks
  // once "none" is picked, so a count typed for Buy/Sell is never sent with it.
  const leaveInput = () => ({
    action: leave.action,
    days: leave.action === "none" ? 0 : Number(leave.days),
  });

  function payload() {
    return {
      elections: tierSets.length
        ? buildElectionsPayload(state, tierSets, dependants, allowDeps)
        : undefined,
      leave: allowLeave ? leaveInput() : undefined,
    };
  }

  return {
    enr,
    isLoading,
    isError,
    error,
    refetch,
    options,
    status: (enr?.status ?? "not_started") as EnrollmentStatus,
    empId,
    dependants,
    tierSets,
    state,
    setProduct: (code: string, next: ProductState) =>
      setState((s) => ({ ...s, [code]: next })),
    leaveAction: leave.action,
    setLeaveAction: (action: string) =>
      setLeave((cur) => ({ action, days: action === "none" ? "0" : cur.days })),
    leaveDays: leave.days,
    setLeaveDays: (days: string) => setLeave((cur) => ({ ...cur, days })),
    leaveInput,
    /** Adopt the next server record wholesale — after an action that replaces
     *  the record on purpose (discarding the choices), not after a refetch. */
    resync: () => {
      seed.current = null;
    },
    flex,
    overdrawn: !!flex && flex.balance < -0.005 && !window.allow_overdraft,
    payload,
  };
}

export type MemberElection = ReturnType<typeof useMemberElection>;
