import { useState } from "react";
import { Loader2, RefreshCw, ShieldQuestion } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageGuide } from "@/components/ui/page-guide";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SkeletonTable } from "@/components/ui/skeleton";
import { StatTile } from "@/components/ui/stat-tile";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useDecideUnderwriting,
  useRefreshUnderwriting,
  useUnderwritingQueue,
  type UnderwritingCase,
} from "@/api/underwriting";
import { useSession } from "@/stores/session";
import { formatError } from "@/lib/errors";
import { fmtCurrency } from "@/lib/format";

const STATUS_BADGE: Record<string, "outline" | "default"> = {
  pending: "default",
  accepted: "outline",
  declined: "outline",
};

function DecisionRow({
  item,
  policyYearId,
}: {
  item: UnderwritingCase;
  policyYearId: string;
}) {
  const decide = useDecideUnderwriting(policyYearId);
  const [status, setStatus] = useState(item.status);
  const [accepted, setAccepted] = useState(String(item.accepted_si));
  const [remarks, setRemarks] = useState(item.remarks ?? "");

  // A blank field must NOT parse to 0 (Number("") === 0) and silently record a
  // zero acceptance — treat empty/whitespace as invalid so Save stays disabled.
  const acceptedTrimmed = accepted.trim();
  const parsedAccepted = Number(acceptedTrimmed);
  const dirty =
    status !== item.status ||
    parsedAccepted !== item.accepted_si ||
    remarks !== (item.remarks ?? "");
  const valid =
    acceptedTrimmed !== "" &&
    Number.isFinite(parsedAccepted) &&
    parsedAccepted >= 0;

  const save = () => {
    decide.mutate(
      {
        caseId: item.id,
        status,
        accepted_si: parsedAccepted,
        remarks: remarks.trim() || null,
      },
      {
        onSuccess: () => toast.success("Decision recorded"),
        onError: (e) => toast.error(formatError(e)),
      },
    );
  };

  return (
    <TableRow>
      <TableCell>
        <div className="font-medium text-foreground">
          {item.subject_name || "—"}
        </div>
        <div className="text-xs text-muted-foreground">
          {item.staff_id}
          {item.subject_type === "dependant" && " · dependant"}
        </div>
      </TableCell>
      <TableCell>{item.product_code}</TableCell>
      <TableCell className="text-right">
        {fmtCurrency(item.eligible_si)}
      </TableCell>
      <TableCell className="text-right text-muted-foreground">
        {item.free_cover_limit != null
          ? fmtCurrency(item.free_cover_limit)
          : "—"}
      </TableCell>
      <TableCell className="text-right">
        {item.pending_si > 0 ? (
          <span className="text-amber-500">{fmtCurrency(item.pending_si)}</span>
        ) : (
          "—"
        )}
      </TableCell>
      <TableCell>
        <Input
          value={accepted}
          onChange={(e) => setAccepted(e.target.value)}
          className="h-8 w-28 text-right text-sm"
          aria-label="Accepted sum insured"
        />
      </TableCell>
      <TableCell>
        <Select
          value={status}
          onValueChange={(v) => setStatus(v as UnderwritingCase["status"])}
        >
          <SelectTrigger className="h-8 w-[120px] text-sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="pending">Pending</SelectItem>
            <SelectItem value="accepted">Accepted</SelectItem>
            <SelectItem value="declined">Declined</SelectItem>
          </SelectContent>
        </Select>
        {item.decided_on && (
          <div className="mt-0.5 text-[11px] text-muted-foreground">
            {item.decided_on}
          </div>
        )}
      </TableCell>
      <TableCell>
        <Input
          value={remarks}
          onChange={(e) => setRemarks(e.target.value)}
          placeholder="Remarks"
          className="h-8 w-40 text-sm"
          aria-label="Remarks"
        />
      </TableCell>
      <TableCell>
        <Button
          size="sm"
          disabled={!dirty || !valid || decide.isPending}
          onClick={save}
        >
          {decide.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            "Save"
          )}
        </Button>
      </TableCell>
    </TableRow>
  );
}

export function UnderwritingPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const { data, isLoading } = useUnderwritingQueue(policyYearId);
  const refresh = useRefreshUnderwriting(policyYearId ?? "");

  const runRefresh = () => {
    refresh.mutate(undefined, {
      onSuccess: (r) =>
        toast.success(
          `Underwriting synced — ${r.opened} opened, ${r.updated} updated, ${r.removed} removed`,
        ),
      onError: (e) => toast.error(formatError(e)),
    });
  };

  return (
    <div className="space-y-5">
      <PageGuide
        purpose="Members (and covered dependants) whose eligible sum insured exceeds a product's free cover limit need the insurer's medical underwriting. Record the insurer's decisions here — insurer listings report the excess as Pending U/W until decided."
        connections={[
          {
            label: "Free cover limits",
            description:
              "Set per product on the Configuration page (product terms row).",
          },
          {
            label: "Reports",
            description:
              "Last Accepted / Pending U/W columns on the insurer employee and dependant listings read these cases.",
          },
        ]}
      />

      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="grid grid-cols-2 gap-3">
          <StatTile label="Cases" value={data?.total ?? 0} />
          <StatTile label="Pending decision" value={data?.pending ?? 0} />
        </div>
        <Button
          variant="outline"
          onClick={runRefresh}
          disabled={refresh.isPending || !policyYearId}
        >
          {refresh.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <RefreshCw className="size-4" />
          )}
          Sync with coverage
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldQuestion className="size-4 text-muted-foreground" />
            Underwriting queue
          </CardTitle>
          <CardDescription>
            Eligible sums insured above the product's free cover limit. Accepted
            amounts are capped at the eligible sum.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <SkeletonTable rows={4} />
          ) : !data?.items.length ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No underwriting cases. Set a free cover limit on a product, then
              “Sync with coverage”.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Member</TableHead>
                  <TableHead>Product</TableHead>
                  <TableHead className="text-right">Eligible SI</TableHead>
                  <TableHead className="text-right">FCL</TableHead>
                  <TableHead className="text-right">Pending U/W</TableHead>
                  <TableHead>Accepted SI</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Remarks</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((item) => (
                  <DecisionRow
                    key={item.id}
                    item={item}
                    policyYearId={policyYearId ?? ""}
                  />
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      {(data?.items.some((i) => i.status !== "pending") ?? false) && (
        <p className="text-xs text-muted-foreground">
          <Badge variant={STATUS_BADGE.accepted} className="mr-1.5">
            decided
          </Badge>
          Decided cases stay listed as the audit trail behind the report
          figures.
        </p>
      )}
    </div>
  );
}
