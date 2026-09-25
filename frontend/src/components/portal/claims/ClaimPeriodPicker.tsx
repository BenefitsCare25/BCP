import { useQuery } from "@tanstack/react-query";
import { portalApi } from "@/api/portalClient";
import { selectedClaimPeriod, setClaimPeriod } from "@/lib/claimPeriod";
import { leafControl } from "@/components/portal/leaf/Field";
import { formatDay } from "@/components/portal/leaf/date";

interface ClaimPeriod { id: string; start_date: string; end_date: string; current: boolean }

/** Which benefit year the claims below belong to. Only rendered when there is
 *  a choice to make — a member in their first year has one period, and a
 *  lone dropdown with one option was the first thing on an empty page. */
export function ClaimPeriodPicker() {
  const periods = useQuery({
    queryKey: ["portal", "claim-periods"],
    queryFn: () => portalApi.get<ClaimPeriod[]>("/portal/claim-periods"),
  });
  const selected = selectedClaimPeriod() ?? "";
  const hasEarlier = (periods.data ?? []).some((period) => !period.current);
  if (!periods.data?.length || (!hasEarlier && !selected)) return null;
  return <div className="mb-5 space-y-1.5">
    <label className="block space-y-1 text-row font-semibold text-record">Benefit year
      <select className={leafControl} value={selected} onChange={(event) => setClaimPeriod(event.target.value)}>
        <option value="">This year</option>
        {periods.data.map((period) => <option key={period.id} value={period.id}>
          {formatDay(period.start_date)} – {formatDay(period.end_date)}{period.current ? " (this year)" : ""}
        </option>)}
      </select>
    </label>
    <p className="text-row text-label">Pick the year your treatment happened in.</p>
  </div>;
}
