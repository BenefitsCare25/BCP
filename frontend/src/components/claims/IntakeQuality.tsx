import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function IntakeQuality() {
  const year = useSession((s) => s.currentPolicyYearId);
  const client = useSession((s) => s.activeClientId);
  const result = useQuery({
    queryKey: ["intake-quality", client, year],
    enabled: !!year,
    queryFn: () => api.get<{ claims: number; suggested_fields: number; corrected_fields: number; correction_rate: number | null; corrections_by_field: Record<string, number> }>(`/claims/intake-quality?policy_year_id=${encodeURIComponent(year!)}`),
  });
  return <Card><CardHeader><CardTitle>Document autofill quality</CardTitle></CardHeader><CardContent className="space-y-2 text-sm">
    {result.isError ? <p>Could not load autofill measurements. <button className="underline" onClick={() => void result.refetch()}>Retry</button></p> : !result.data ? <p>Loading measurements…</p> : <>
      <p>{result.data.claims} submitted claims used recorded autofill suggestions. {result.data.corrected_fields} of {result.data.suggested_fields} suggested fields were changed or omitted before submission{result.data.correction_rate == null ? "." : ` (${(result.data.correction_rate * 100).toFixed(1)}%).`}</p>
      <p className="text-muted-foreground">Selected benefit year. Measures initial member adjustments, not model accuracy; historical claims without a recorded suggestion are excluded.</p>
      {!!Object.keys(result.data.corrections_by_field).length && <ul className="list-disc pl-5">{Object.entries(result.data.corrections_by_field).map(([field, count]) => <li key={field}>{field.replaceAll("_", " ")}: {count}</li>)}</ul>}
    </>}
  </CardContent></Card>;
}
