import { usePortalTranslation } from "@/i18n/portal";
import { getPortalLocale } from "@/i18n/portal";
import { Link } from "@tanstack/react-router";
import { useEnrollmentNotices, useReadEnrollmentNotice } from "@/api/enrollmentEvents";
import { useCompany } from "@/components/portal/useCompany";
import { NotificationBell } from "@/components/shell/NotificationBell";
import { Mount } from "@/components/portal/leaf/Mount";
import { formatDay } from "@/components/portal/leaf/date";

function deadline(value: string) {
  return `${new Date(value).toLocaleString(getPortalLocale(), { timeZone: "Asia/Singapore", day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" })} SGT`;
}

export function EnrollmentNoticeList({ compact = false }: { compact?: boolean }) {
  const pt = usePortalTranslation();
  const notices = useEnrollmentNotices();
  const read = useReadEnrollmentNotice();
  const company = useCompany();
  if (notices.isPending) return <p className="p-3 text-row text-label">{pt("Loading enrolment updates…")}</p>;
  if (notices.isError) return <button className="leaf-focus min-h-11 px-3 text-row text-action-ink" onClick={() => void notices.refetch()}>{pt("Enrolment updates unavailable. Retry")}</button>;
  const items = notices.data.items;
  if (!items.length) return null;
  return <ol className="divide-y divide-hairline">
    {items.slice(0, compact ? 10 : 50).map((event) => <li key={event.id} className="space-y-1.5 p-3 text-row">
      <p className="font-semibold text-record">{pt(event.title)}{!event.read_at && <span className="ml-2 text-action-ink">{pt("· Unread")}</span>}</p>
      <p className="text-label">{pt(event.window_name)} · {formatDay(event.created_at)}</p>
      <p className="text-record">{pt(event.message)}</p>
      {event.reason && <p className="whitespace-pre-wrap break-words text-record"><span className="font-semibold">{pt("Reason:")} </span>{event.reason}</p>}
      {["returned", "cancelled", "reopened", "revised"].includes(event.kind) && <p className="text-label">{pt("Period deadline:")} {deadline(event.closes_at)}{pt(". If it has passed, contact HR.")}</p>}
      <div className="flex flex-wrap gap-x-4">
        {compact && <Link to="/portal/$company/enrollment" params={{ company }} className="leaf-focus inline-flex min-h-11 items-center text-action-ink">{pt("View enrolment")}</Link>}
        {!event.read_at && <button className="leaf-focus min-h-11 text-action-ink" disabled={read.isPending} onClick={() => read.mutate(event.id)}>{pt("Mark as read")}</button>}
      </div>
    </li>)}
  </ol>;
}

export function PortalNotificationBell() {
  usePortalTranslation();
  const notices = useEnrollmentNotices();
  return <NotificationBell persistentUnread={notices.data?.unread ?? 0} persistent={<EnrollmentNoticeList compact />} />;
}

export function EnrollmentUpdates() {
  const pt = usePortalTranslation();
  const notices = useEnrollmentNotices();
  if (notices.isSuccess && !notices.data.items.length) return null;
  return <Mount label={pt("Enrolment updates")}><EnrollmentNoticeList /></Mount>;
}

export function EnrollmentStatusNotice({ enrollmentId }: { enrollmentId?: string }) {
  const pt = usePortalTranslation();
  const notices = useEnrollmentNotices();
  const event = notices.data?.items.find((item) => item.enrollment_id === enrollmentId);
  if (!event) return null;
  return <Mount label={pt(event.title)}>
    <p className="text-row text-record">{pt(event.message)}</p>
    {event.reason && <p className="whitespace-pre-wrap break-words text-row text-record"><span className="font-semibold">{pt("Reason:")} </span>{event.reason}</p>}
  </Mount>;
}
