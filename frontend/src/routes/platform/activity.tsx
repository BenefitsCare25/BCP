import { useState } from "react";
import {
  type ActivityPeriod,
  type PlatformActivity,
  fmtCount,
  signInTotal,
  usePlatformActivity,
} from "@/api/platformActivity";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { StatTile } from "@/components/ui/stat-tile";
import { ActivityPeriodPicker } from "@/components/platform/ActivityPeriodPicker";
import { ActivityTable } from "@/components/platform/ActivityTable";
import { ActivityTrend } from "@/components/platform/ActivityTrend";
import { PlatformPageHeader } from "@/components/platform/PlatformPageHeader";
import { ListError, ListLoading } from "@/components/platform/QueryStates";
import { fmtDateTime } from "@/lib/format";

function SummaryTiles({ data }: { data: PlatformActivity }) {
  const { totals } = data;
  const { sign_ins: s } = totals;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <StatTile
        label="Brokers active"
        value={`${totals.firms_with_activity} of ${totals.firms}`}
        caption="Signed in or submitted"
      />
      <StatTile
        label="Companies"
        value={totals.companies}
        formatNumber
        caption={`${fmtCount(totals.companies_portal_enabled)} on the employee portal`}
      />
      <StatTile label="Members active" value={totals.members_active} formatNumber />
      <StatTile
        label="Sign-ins"
        value={signInTotal(s)}
        formatNumber
        caption={`Staff ${fmtCount(s.staff)} · HR ${fmtCount(s.hr)} · Members ${fmtCount(s.member)}`}
      />
      <StatTile
        label="Claims submitted"
        value={fmtCount(totals.claims_submitted)}
        caption={`${fmtCount(totals.claims_open)} open now`}
      />
      <StatTile label="Enrolments submitted" value={fmtCount(totals.enrolments_submitted)} />
    </div>
  );
}

function UnavailableNote({ count }: { count: number }) {
  if (count === 0) return null;
  return (
    <p role="status" className="text-sm text-muted-foreground">
      {count === 1 ? "One broker's" : `${count} brokers'`} claim, enrolment and AI counts
      could not be read. They show as — and are left out of the totals.
    </p>
  );
}

function ActivityContent({ data }: { data: PlatformActivity }) {
  if (data.firms.length === 0) {
    return (
      <Card>
        <p className="p-5 text-sm text-muted-foreground">No broker firms yet.</p>
      </Card>
    );
  }
  return (
    <>
      <SummaryTiles data={data} />
      <UnavailableNote count={data.totals.firms_unavailable} />
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Daily activity</CardTitle>
          <CardDescription>Every broker, by UTC day.</CardDescription>
        </CardHeader>
        <CardContent>
          <ActivityTrend
            caption={`Platform activity per day, last ${data.days} days`}
            daily={data.daily}
            series={["sign_ins", "claims_submitted", "enrolments_submitted"]}
          />
        </CardContent>
      </Card>
      <Card className="overflow-hidden">
        <ActivityTable firms={data.firms} days={data.days} />
      </Card>
      <p className="text-xs text-muted-foreground">
        Counted {fmtDateTime(data.generated_at)}. Figures refresh every two minutes.
      </p>
    </>
  );
}

export function PlatformActivityPage() {
  const [days, setDays] = useState<ActivityPeriod>(30);
  const activity = usePlatformActivity(days);

  return (
    <>
      <PlatformPageHeader
        title="Activity"
        description="How each broker is using the platform: companies, people, sign-ins, claims and enrolments. Counts only, with no personal details."
        actions={<ActivityPeriodPicker value={days} onChange={setDays} />}
      />
      {activity.isPending ? (
        <ListLoading label="Loading activity…" />
      ) : activity.isError ? (
        <ListError what="activity" error={activity.error} onRetry={() => void activity.refetch()} />
      ) : (
        <div className="space-y-5" aria-busy={activity.isPlaceholderData}>
          <ActivityContent data={activity.data} />
        </div>
      )}
    </>
  );
}
