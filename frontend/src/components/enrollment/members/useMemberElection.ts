/** One member's election: the server record, the broker's on-screen edits,
 * the live flex maths and the payload those edits would send. */
import { useEffect, useMemo, useState } from "react";
import { useBenefitStatement } from "@/api/hooks";
import {
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
  seedElectionState,
} from "@/components/enrollment/electionCore";

export function useMemberElection(enrollmentId: string, window: EnrollmentWindow) {
  const allowLeave = window.allow_leave;
  const allowDeps = window.allow_dependant_changes;
  const { data: enr, isLoading } = useEnrollment(enrollmentId);
  const { data: options } = useEnrollmentOptions(enrollmentId);
  const empId = enr?.employee_id ?? null;
  const { data: statement } = useBenefitStatement(allowDeps ? empId : null);
  const dependants = useMemo(() => statement?.dependants ?? [], [statement]);

  const productScope = window.product_scope;
  const tierSets = useMemo<ProductTierSet[]>(() => {
    const all = options?.products ?? [];
    const scope = productScope?.length ? new Set(productScope) : null;
    return scope ? all.filter((p) => scope.has(p.product_code)) : all;
  }, [options, productScope]);

  const [state, setState] = useState<Record<string, ProductState>>({});
  const [leaveAction, setLeaveAction] = useState("none");
  const [leaveDays, setLeaveDays] = useState("0");

  useEffect(() => {
    if (!enr || !options) return;
    setState(seedElectionState(enr, tierSets));
    setLeaveAction(enr.leave?.action ?? "none");
    setLeaveDays(String(enr.leave?.days ?? 0));
  }, [enr, options, tierSets]);

  const flex = computeFlex(
    options, tierSets, state, dependants, allowDeps, leaveAction, leaveDays,
    options?.leave ?? null,
  );

  function payload() {
    return {
      elections: tierSets.length
        ? buildElectionsPayload(state, tierSets, dependants, allowDeps)
        : undefined,
      leave: allowLeave ? { action: leaveAction, days: Number(leaveDays) } : undefined,
    };
  }

  return {
    enr,
    isLoading,
    options,
    status: (enr?.status ?? "not_started") as EnrollmentStatus,
    empId,
    dependants,
    tierSets,
    state,
    setProduct: (code: string, next: ProductState) =>
      setState((s) => ({ ...s, [code]: next })),
    leaveAction,
    setLeaveAction,
    leaveDays,
    setLeaveDays,
    flex,
    overdrawn: !!flex && flex.balance < -0.005 && !window.allow_overdraft,
    payload,
  };
}

export type MemberElection = ReturnType<typeof useMemberElection>;
