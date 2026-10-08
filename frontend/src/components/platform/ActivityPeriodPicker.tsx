import { ACTIVITY_PERIODS, type ActivityPeriod } from "@/api/platformActivity";
import { Segmented } from "@/components/ui/segmented";

const OPTIONS = ACTIVITY_PERIODS.map((days) => ({
  value: String(days),
  label: `${days} days`,
}));

/** 7 / 30 / 90-day period for the activity views. */
export function ActivityPeriodPicker({
  value,
  onChange,
}: {
  value: ActivityPeriod;
  onChange: (days: ActivityPeriod) => void;
}) {
  return (
    <div role="group" aria-label="Activity period">
      <Segmented
        value={String(value)}
        onChange={(v) => onChange(Number(v) as ActivityPeriod)}
        options={OPTIONS}
      />
    </div>
  );
}
