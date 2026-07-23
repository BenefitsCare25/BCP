import { useState } from "react";
import { toast } from "sonner";
import { Loader2, Plus } from "lucide-react";
import { useCreateClient, useDashboardSummary } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { formatError } from "@/lib/errors";

// Firm-level company management. Adding a company is deliberately the ONLY entry
// point here (not on the Home roster) so the create action lives with standing
// configuration rather than the day-to-day pick-a-company view.
export function CompaniesSettingsCard() {
  const { data, isLoading } = useDashboardSummary();
  const create = useCreateClient();
  const [name, setName] = useState("");

  const companies = data?.companies ?? [];

  const onCreate = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      await create.mutateAsync(trimmed);
      toast.success("Company created");
      setName("");
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Companies</CardTitle>
        <CardDescription>
          Every company your firm manages. Add a new company to onboard its
          benefits, members and claims.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onCreate()}
            placeholder="New company name"
            className="sm:max-w-xs"
          />
          <Button onClick={onCreate} disabled={create.isPending || !name.trim()}>
            {create.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Plus className="size-4" />
            )}
            Add company
          </Button>
        </div>

        {isLoading ? (
          <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading companies…
          </div>
        ) : companies.length === 0 ? (
          <p className="py-4 text-sm text-muted-foreground">
            No companies yet — add your first above.
          </p>
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border">
            {companies.map((c) => (
              <li
                key={c.id}
                className="flex items-center gap-3 px-3 py-2.5 text-sm"
              >
                <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-accent text-xs font-semibold text-accent-foreground">
                  {c.name.trim().charAt(0).toUpperCase() || "?"}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium text-foreground">
                    {c.name}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {c.current_year
                      ? `${c.current_year.year} · Current`
                      : "No current year"}
                    {" · "}
                    {c.member_count} members
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
