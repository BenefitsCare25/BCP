import { useState } from "react";
import {
  type ActivityPeriod,
  type FirmActivity,
  fmtCount,
  signInTotal,
  useFirmDailyActivity,
  usePlatformActivity,
} from "@/api/platformActivity";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { fmtDateTime } from "@/lib/format";
import { ActivityPeriodPicker } from "./ActivityPeriodPicker";
import { ActivityTrend } from "./ActivityTrend";
import { ListError, ListLoading } from "./QueryStates";

function Counter({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-lg font-semibold tabular-nums text-foreground">{value}</dd>
      {detail && <dd className="text-xs text-muted-foreground">{detail}</dd>}
    </div>
  );
}

function Counters({ firm }: { firm: FirmActivity }) {
  const { sign_ins: s } = firm;
  return (
    <dl className="grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-3 lg:grid-cols-5">
      <Counter
        label="Companies"
        value={fmtCount(firm.companies)}
        detail={`${fmtCount(firm.companies_portal_enabled)} on the employee portal`}
      />
      <Counter
        label="Staff"
        value={fmtCount(firm.staff_active)}
        detail={`${fmtCount(firm.staff_invited)} invited`}
      />
      <Counter label="HR users" value={fmtCount(firm.hr_users)} />
      <Counter label="Members active" value={fmtCount(firm.members_active)} />
      <Counter
        label="Sign-ins"
        value={fmtCount(signInTotal(s))}
        detail={`Staff ${fmtCount(s.staff)} · HR ${fmtCount(s.hr)} · Members ${fmtCount(s.member)}`}
      />
      <Counter label="Live sessions" value={fmtCount(firm.active_sessions)} />
      <Counter
        label="Claims submitted"
        value={fmtCount(firm.claims_submitted)}
        detail={`${fmtCount(firm.claims_open)} open now`}
      />
      <Counter label="Enrolments submitted" value={fmtCount(firm.enrolments_submitted)} />
      <Counter label="AI tokens" value={fmtCount(firm.ai_tokens)} />
      <Counter
        label="Last activity"
        value={firm.last_activity_at ? fmtDateTime(firm.last_activity_at) : "None yet"}
      />
    </dl>
  );
}

function FirmTrend({ firmId, firmName, days }: { firmId: string; firmName: string; days: ActivityPeriod }) {
  const daily = useFirmDailyActivity(firmId, days);
  if (daily.isPending) return <ListLoading label="Loading daily activity…" />;
  if (daily.isError) {
    return (
      <ListError what="daily activity" error={daily.error} onRetry={() => void daily.refetch()} />
    );
  }
  return (
    <ActivityTrend
      caption={`${firmName} activity per day, last ${days} days`}
      daily={daily.data.daily}
      series={["sign_ins", "claims_submitted"]}
      height="h-32"
    />
  );
}

/** A broker's activity counters and daily trend, as on the Activity page. */
export function FirmActivityCard({ firmId, firmName }: { firmId: string; firmName: string }) {
  const [days, setDays] = useState<ActivityPeriod>(30);
  const activity = usePlatformActivity(days);
  const firm = activity.data?.firms.find((f) => f.firm_id === firmId);

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <CardTitle className="text-sm">Activity</CardTitle>
          <CardDescription>
            Counts for the last {days} days, by UTC day. No personal details.
          </CardDescription>
        </div>
        <ActivityPeriodPicker value={days} onChange={setDays} />
      </CardHeader>
      <CardContent className="space-y-6">
        {activity.isPending ? (
          <ListLoading label="Loading activity…" />
        ) : activity.isError ? (
          <ListError what="activity" error={activity.error} onRetry={() => void activity.refetch()} />
        ) : firm ? (
          <Counters firm={firm} />
        ) : (
          <p className="text-sm text-muted-foreground">No activity recorded for this firm yet.</p>
        )}
        <FirmTrend firmId={firmId} firmName={firmName} days={days} />
      </CardContent>
    </Card>
  );
}
