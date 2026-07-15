/** Broker queue for portal self-added dependants awaiting approval.
 * Approval activates the dependant and re-runs flex assignment server-side. */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, UserCheck, X } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";
import { formatError } from "@/lib/errors";
import { fmtDate } from "@/lib/format";
import type { Dependant, DependantList } from "@/types";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { InfoHint } from "@/components/ui/tooltip";

interface ProofDoc {
  id: string;
  file_name: string;
  size_bytes: number;
}

/** Exported so the roster tab host can badge the Dependants tab with the
 * pending count (shared query key → one request). */
export function usePendingDependants(policyYearId: string) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["dependants", "pending", policyYearId, cid],
    queryFn: () =>
      api.get<DependantList>(
        `/dependants?policy_year_id=${policyYearId}&status=pending_approval&limit=200`,
      ),
    enabled: Boolean(policyYearId),
  });
}

/** Approval response — flex_errors lists flex re-assignment problems the
 * broker must follow up on (absent on older backends). */
type DependantDecision = Dependant & { flex_errors?: string[] };

function useDecideDependant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      dependantId: string;
      action: "approve" | "reject";
      note?: string;
    }) =>
      api.post<DependantDecision>(`/dependants/${input.dependantId}/approval`, {
        action: input.action,
        note: input.note ?? null,
      }),
    onSuccess: () => {
      for (const key of ["dependants", "benefit-statement", "flex-membership"]) {
        void qc.invalidateQueries({ queryKey: [key] });
      }
    },
    meta: { localErrorHandling: true },
  });
}

export function DependantApprovals({ policyYearId }: { policyYearId: string }) {
  const pending = usePendingDependants(policyYearId);
  const decide = useDecideDependant();
  const [confirm, setConfirm] = useState<{
    dependant: Dependant;
    action: "approve" | "reject";
  } | null>(null);

  const rows = pending.data?.items ?? [];
  if (pending.isLoading || rows.length === 0) return null;

  const act = async () => {
    if (!confirm) return;
    try {
      const res = await decide.mutateAsync({
        dependantId: confirm.dependant.id,
        action: confirm.action,
      });
      if (confirm.action !== "approve") {
        toast.success("Dependant rejected");
      } else if (res.flex_errors?.length) {
        toast.warning(
          `Dependant approved, but flex assignment needs attention: ${res.flex_errors[0]}`,
        );
      } else {
        toast.success("Dependant approved — flex assignment refreshed");
      }
    } catch (err) {
      toast.error(formatError(err));
    } finally {
      setConfirm(null);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <UserCheck className="size-4 text-warn" />
          <div>
            <div className="flex items-center gap-1">
              <CardTitle>Pending dependant approvals</CardTitle>
              <InfoHint>
                Approving activates coverage and refreshes the employee's flex
                wallet. Rejecting shows the member a rejected status in their
                portal.
              </InfoHint>
            </div>
            <CardDescription>
              {rows.length} member-submitted dependant{rows.length === 1 ? "" : "s"}{" "}
              awaiting review.
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {rows.map((dep) => (
          <PendingRow
            key={dep.id}
            dep={dep}
            onDecide={(action) => setConfirm({ dependant: dep, action })}
          />
        ))}
      </CardContent>

      <AlertDialog
        open={confirm !== null}
        onOpenChange={(o) => {
          if (!o) setConfirm(null);
        }}
        title={
          confirm?.action === "approve" ? "Approve dependant?" : "Reject dependant?"
        }
        description={
          confirm ? (
            <>
              <strong>{String(confirm.dependant.attribute_values.name ?? "This dependant")}</strong>{" "}
              {confirm.action === "approve"
                ? "will become an active covered dependant. Family status and the flex wallet may change."
                : "will be rejected — the member will see the rejected status in their portal."}
            </>
          ) : null
        }
        confirmLabel={confirm?.action === "approve" ? "Approve" : "Reject"}
        confirmVariant={confirm?.action === "approve" ? "default" : "destructive"}
        loading={decide.isPending}
        onConfirm={() => void act()}
      />
    </Card>
  );
}

function PendingRow({
  dep,
  onDecide,
}: {
  dep: Dependant;
  onDecide: (action: "approve" | "reject") => void;
}) {
  const docs = useQuery({
    queryKey: ["dependants", "proof-docs", dep.id],
    queryFn: () => api.get<ProofDoc[]>(`/dependants/${dep.id}/documents`),
  });

  const download = async (doc: ProofDoc) => {
    try {
      const blob = await api.download(
        `/dependants/${dep.id}/documents/${doc.id}/download`,
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = doc.file_name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  const av = dep.attribute_values;
  return (
    <div className="rounded-md border border-border bg-card p-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-foreground">
              {String(av.name ?? "—")}
            </span>
            <Badge variant="outline" className="capitalize">
              {String(av.relationship ?? "—")}
            </Badge>
            {av.dob != null && (
              <span className="text-xs text-muted-foreground">
                DOB {fmtDate(String(av.dob))}
              </span>
            )}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            {(docs.data ?? []).map((doc) => (
              <button
                key={doc.id}
                type="button"
                onClick={() => void download(doc)}
                className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 text-xs text-foreground hover:bg-muted/70"
              >
                <Download className="size-3" /> {doc.file_name}
              </button>
            ))}
            {docs.data && docs.data.length === 0 && (
              <span className="text-xs text-muted-foreground">
                No proof document attached
              </span>
            )}
            {docs.isError && (
              <span className="text-xs text-error">
                Couldn't load proof documents —{" "}
                <button
                  type="button"
                  onClick={() => void docs.refetch()}
                  className="underline hover:no-underline"
                >
                  retry
                </button>
              </span>
            )}
          </div>
        </div>
        <div className="flex gap-2">
          <Button size="sm" onClick={() => onDecide("approve")}>
            <Check className="size-4" /> Approve
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="text-error hover:text-error"
            onClick={() => onDecide("reject")}
          >
            <X className="size-4" /> Reject
          </Button>
        </div>
      </div>
    </div>
  );
}
