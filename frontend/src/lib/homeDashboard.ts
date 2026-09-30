import { useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import type { CompanySummary } from "@/api/dashboard";
import { calendarDaysUntil, daysUntil, parseServerDate } from "@/lib/attention";
import { useSession } from "@/stores/session";

export type QueueKey =
  | "claims_review"
  | "messages"
  | "insurer"
  | "underwriting"
  | "enrollment"
  | "matching"
  | "dependants";

export type CompanyDestination = QueueKey | "dashboard" | "overdue" | "setup";

export type WorkQueue = {
  key: QueueKey;
  label: string;
  description: string;
  count: (company: CompanySummary) => number;
  hasWork?: (company: CompanySummary) => boolean;
};

export const WORK_QUEUES: WorkQueue[] = [
  {
    key: "claims_review",
    label: "Pending Claims",
    description:
      "Insurer and Flex claims awaiting human review, including claims the AI could not complete.",
    count: (company) => company.claims_to_review,
  },
  {
    key: "messages",
    label: "New Message",
    description:
      "Employee conversations where the employee sent the latest message and is waiting for a reply.",
    count: (company) => company.messages_awaiting_reply,
  },
  {
    key: "insurer",
    label: "Pending Insurer",
    description: "Claims sent to insurers and awaiting their response.",
    count: (company) => company.claims_with_insurer,
  },
  {
    key: "underwriting",
    label: "Pending UW",
    description:
      "Open underwriting reviews awaiting requirements, an employee, the insurer, or HR; includes employees and dependants.",
    count: (company) => company.underwriting_pending,
  },
  {
    key: "enrollment",
    label: "Enrollment",
    description: "Benefit years with open, scheduled, or overdue enrollment windows.",
    count: (company) => (company.enrollment_open_count ?? Number(company.enrollment_open)) + (company.enrollment_scheduled ?? 0) + (company.enrollment_overdue ?? 0),
  },
  {
    key: "matching",
    label: "Member matching",
    description:
      "Companies with unmatched employees or matching results made stale by category changes.",
    count: (company) => company.employees_unmatched,
    hasWork: (company) =>
      company.employees_unmatched > 0 || company.matching_stale,
  },
  {
    key: "dependants",
    label: "Dependant approvals",
    description: "Dependants awaiting review and approval.",
    count: (company) => company.dependants_pending,
  },
];

export function useCompanyNavigation() {
  const navigate = useNavigate();
  const setActiveClient = useSession((state) => state.setActiveClient);
  const setPolicyYear = useSession((state) => state.setPolicyYear);
  const queryClient = useQueryClient();
  return (company: CompanySummary, destination: CompanyDestination, kind?: "insured" | "flex") => {
    setActiveClient(company.id);
    setPolicyYear(company.current_year?.id ?? null);
    queryClient.removeQueries();
    if (destination === "setup") {
      navigate({ to: "/client-relations/company-benefits" });
    } else if (destination === "dashboard") {
      navigate({ to: "/dashboard" });
    } else if (destination === "enrollment") {
      navigate({ to: "/client-relations/enrollment" });
    } else if (destination === "underwriting") {
      navigate({ to: "/policy-admin/underwriting" });
    } else if (destination === "matching") {
      navigate({ to: "/policy-admin/member-listing" });
    } else if (destination === "dependants") {
      navigate({ to: "/policy-admin/member-listing", search: { tab: "dependants" } });
    } else if (destination === "messages") {
      navigate({ to: "/claims/review", search: { tab: "messages", awaiting: "us" } });
    } else {
      navigate({ to: "/claims/review", search: {
        tab: "queue",
        queue: destination === "claims_review" ? "review" : destination,
        kind,
      } });
    }
  };
}

export function renewalCompanies(companies: CompanySummary[], horizon: number, businessDate?: string) {
  return companies
    .filter((company) => company.current_year?.end_date)
    .filter((company) => {
      const days = policyDaysUntil(company.current_year!.end_date, businessDate);
      return days >= 0 && days <= horizon && periodState(company, businessDate) === "Current";
    })
    .sort((a, b) => a.current_year!.end_date.localeCompare(b.current_year!.end_date));
}

export function policyDaysUntil(value: string, businessDate?: string) {
  if (!businessDate) return calendarDaysUntil(value);
  const [year, month, day] = businessDate.split("-").map(Number);
  return calendarDaysUntil(value, new Date(year, month - 1, day));
}

export function periodState(company: CompanySummary, businessDate?: string) {
  if (!company.current_year) return "No benefit year";
  if (policyDaysUntil(company.current_year.start_date, businessDate) > 0) return "Upcoming";
  if (policyDaysUntil(company.current_year.end_date, businessDate) < 0) return "Expired";
  return "Current";
}

export function enrollmentStatus(company: CompanySummary) {
  const states = [];
  if (company.enrollment_overdue) states.push(`${company.enrollment_overdue} awaiting closure`);
  if (company.enrollment_open) states.push(company.enrollment_closes_at
    ? enrollmentClosingLabel(company.enrollment_closes_at) : "Open");
  if (company.enrollment_scheduled) states.push(company.enrollment_opens_at
    ? `Opens ${new Intl.DateTimeFormat("en-SG", {
      day: "numeric", month: "short", timeZone: "Asia/Singapore",
    }).format(parseServerDate(company.enrollment_opens_at))}`
    : `${company.enrollment_scheduled} scheduled`);
  return states.join(" · ") || "Closed";
}

export function queuePriority(company: CompanySummary, queue: QueueKey) {
  if (queue === "enrollment") {
    return (company.enrollment_overdue ?? 0) * 10_000 + Number(company.enrollment_open) * 1_000;
  }
  if (queue === "insurer") {
    return company.claims_overdue * 10_000 + company.claims_with_insurer;
  }
  return WORK_QUEUES.find((item) => item.key === queue)?.count(company) ?? 0;
}

export function queuePriorityLabel(company: CompanySummary, queue: QueueKey) {
  if (queue === "insurer" && company.claims_overdue > 0) {
    return { label: `${company.claims_overdue} overdue`, overdue: true };
  }
  if (queue === "matching" && company.matching_stale) {
    return { label: "Matching stale", overdue: false };
  }
  if (queue === "messages") {
    return { label: "Needs reply", overdue: false };
  }
  return { label: "Open", overdue: false };
}

export function queueLabel(queue: QueueKey) {
  return WORK_QUEUES.find((item) => item.key === queue)?.label ?? "work";
}

export function enrollmentClosingLabel(closesAt: string) {
  const days = daysUntil(closesAt);
  if (days < 0) return "Past closing date";
  if (days === 0) return "Closes today";
  if (days === 1) return "Closes tomorrow";
  return `Closes in ${days} days`;
}

export function policyPeriodLabel(policyYear: CompanySummary["current_year"]) {
  if (!policyYear) return "—";
  return `${formatPolicyDate(policyYear.start_date)} – ${formatPolicyDate(policyYear.end_date)}`;
}

function formatPolicyDate(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return value;
  return new Intl.DateTimeFormat("en-SG", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(year, month - 1, day)));
}
