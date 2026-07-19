/** Panel setup history — what clinic networks and e-cards each benefit year had.
 *
 * The library (clinic lists, card artwork) is year-independent, so it is NOT
 * shown here: this answers the year-scoped question "what did members actually
 * see in 2025?", which is exactly what carries over on renewal.
 */
import { History, Loader2, MapPin } from "lucide-react";
import {
  usePanelSetupHistory,
  type SetupHistoryYear,
} from "@/api/panelCards";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { formatPolicyRange } from "@/lib/policy-year";

const SOURCE_LABELS: Record<string, string> = {
  insurer_member_id: "Insurer member ID",
  staff_id: "Staff ID",
  email: "Email",
  national_id_masked: "NRIC (masked)",
  platform_id: "Platform ID",
};

const REMARK_LABELS: Record<string, string> = {
  gp: "GP",
  ae: "A&E",
  restructured_sp: "Restructured SP",
  private_sp: "Private SP",
  general: "General",
};

function YearBlock({ entry }: { entry: SetupHistoryYear }) {
  const empty = entry.listings.length === 0 && entry.cards.length === 0;
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-foreground">
            {entry.year}
          </span>
          <span className="text-xs text-muted-foreground">
            {formatPolicyRange(entry.start_date, entry.end_date)}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {entry.is_current ? (
            <Badge variant="good">Current — members see this</Badge>
          ) : (
            <Badge variant="outline">{entry.status}</Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {empty ? (
          <p className="text-xs text-muted-foreground">
            No clinic networks or e-cards were set up for this benefit year.
          </p>
        ) : (
          <>
            <div className="space-y-1.5">
              <p className="text-xs font-medium text-foreground">
                Clinic networks ({entry.listings.length})
              </p>
              {entry.listings.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  None enabled — the clinic locator was empty for members.
                </p>
              ) : (
                <ul className="space-y-1">
                  {entry.listings.map((listing) => (
                    <li
                      key={listing.id}
                      className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-1.5"
                    >
                      <span className="flex items-center gap-2 text-sm text-foreground">
                        <MapPin className="size-3.5 text-muted-foreground" />
                        {listing.display_label}
                        <Badge variant="outline">{listing.type_label}</Badge>
                      </span>
                      <span className="text-xs tabular-nums text-muted-foreground">
                        {listing.clinic_count} clinics
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="space-y-1.5">
              <p className="text-xs font-medium text-foreground">
                E-cards issued ({entry.cards.length})
              </p>
              {entry.cards.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  None issued — members had no digital panel card.
                </p>
              ) : (
                <ul className="space-y-1">
                  {entry.cards.map((card) => (
                    <li
                      key={card.id}
                      className="space-y-1 rounded-md border border-border px-3 py-2"
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-sm text-foreground">
                          {card.product_name}{" "}
                          <span className="text-muted-foreground">
                            ({card.product_code})
                          </span>
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {card.card_name}
                        </span>
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-xs text-muted-foreground">
                          Member ID:{" "}
                          {SOURCE_LABELS[card.employee_member_id_source] ??
                            card.employee_member_id_source}
                          {card.dependant_member_id_source !==
                            card.employee_member_id_source && (
                            <>
                              {" · dependants: "}
                              {SOURCE_LABELS[card.dependant_member_id_source] ??
                                card.dependant_member_id_source}
                            </>
                          )}
                        </span>
                        {card.service_labels.map((label) => (
                          <Badge key={label} variant="outline">
                            {label}
                          </Badge>
                        ))}
                      </div>
                      {(card.remark_keys.length > 0 ||
                        card.special_conditions) && (
                        <p className="text-xs text-muted-foreground">
                          {card.remark_keys.length > 0 && (
                            <>
                              Remarks:{" "}
                              {card.remark_keys
                                .map((k) => REMARK_LABELS[k] ?? k)
                                .join(", ")}
                            </>
                          )}
                          {card.remark_keys.length > 0 &&
                            card.special_conditions &&
                            " · "}
                          {card.special_conditions && (
                            <>Special conditions: {card.special_conditions}</>
                          )}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function PanelSetupHistory() {
  const { data, isLoading } = usePanelSetupHistory();
  const years = data?.years ?? [];

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading setup history…
      </div>
    );
  }
  if (years.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border p-8 text-center">
        <History className="mx-auto size-6 text-muted-foreground" />
        <p className="mt-2 text-sm font-medium text-foreground">
          No benefit years yet
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Create a benefit year on the Configuration page, then enable clinic
          networks and issue e-cards for it.
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Newest first. Clinic lists and card artwork live in a shared library and
        are not year-specific — only the selections below are, and they carry
        over automatically when a new benefit year is created.
      </p>
      {years.map((entry) => (
        <YearBlock key={entry.policy_year_id} entry={entry} />
      ))}
    </div>
  );
}
