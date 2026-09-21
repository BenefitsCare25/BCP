import { useQuery } from "@tanstack/react-query";
import { portalApi } from "@/api/portalClient";
import { selectedClaimPeriod, setClaimPeriod } from "@/lib/claimPeriod";
import { leafControl } from "@/components/portal/leaf/Field";

interface ClaimPeriod { id: string; start_date: string; end_date: string; current: boolean }

export function ClaimPeriodPicker() {
  const periods = useQuery({
    queryKey: ["portal", "claim-periods"],
    queryFn: () => portalApi.get<ClaimPeriod[]>("/portal/claim-periods"),
  });
  if (!periods.data?.length) return null;
  const selected = selectedClaimPeriod() ?? "";
  return <div className="mb-5 space-y-2">
    <label className="block space-y-1 text-sm text-label">Claim benefit period
      <select className={leafControl} value={selected} onChange={(event) => setClaimPeriod(event.target.value)}>
        <option value="">Current benefit year</option>
        {periods.data.map((period) => <option key={period.id} value={period.id}>
          {period.start_date} to {period.end_date}{period.current ? " (current)" : " (previous)"}
        </option>)}
      </select>
    </label>
    <p className="text-xs text-label">Choose the period covering your treatment. Each product keeps its own coverage dates and submission deadline. Existing claims remain in their original period.</p>
  </div>;
}
