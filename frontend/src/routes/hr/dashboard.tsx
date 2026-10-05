/** HR admin home — identity and entry points to operational modules. Claims
 * and enrolment forms are live; the remaining modules stay visibly unavailable
 * until ready. */
import { Link } from "@tanstack/react-router";
import {
  ArrowRight,
  ClipboardList,
  FileSignature,
  FileText,
  ShieldAlert,
  Users,
} from "lucide-react";
import { useHrMe } from "@/api/hr";
import { useHrSession } from "@/stores/hrSession";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { SkyStage, skyPeriod } from "@/components/portal/home/SkyStage";

const MODULES = [
  {
    key: "employees",
    title: "Employees",
    description: "Your company's roster, coverage and dependants.",
    icon: Users,
  },
  {
    key: "claims",
    title: "Claims",
    description: "Submit and track claims for employees.",
    icon: ClipboardList,
    href: "/hr/claims" as const,
  },
  {
    key: "enrollment-forms",
    title: "Enrolment forms",
    description: "Download filed forms as PDF, ZIP or Excel.",
    icon: FileSignature,
    href: "/hr/enrollment-forms" as const,
  },
  {
    key: "policies",
    title: "Policies",
    description: "Benefit schedules and policy documents for your plans.",
    icon: FileText,
  },
] as const;

export function HrDashboardPage() {
  const me = useHrSession((s) => s.me);
  const { data } = useHrMe();
  const identity = data ?? me;
  const name = identity?.display_name?.trim() || "there";
  // Optional-2FA nudge, derived from the live identity: the company has 2FA
  // available and this person hasn't confirmed enrolment yet. Deriving it (not
  // storing a session flag) means it stays correct across token refresh and
  // updates the instant enrolment completes.
  const mfaSuggested =
    !!identity?.mfa_available && identity?.mfa_status !== "confirmed";

  return (
    <div className="hr-overview">
      <SkyStage period={skyPeriod()} className="hr-overview-sky" label="Welcome">
        {identity?.company_name && <p className="sky-eyebrow">{identity.company_name}</p>}
        <h1>Welcome, <span className="name">{name}</span></h1>
        <p className="sky-sub">Your employee claims and enrolment forms in one place.</p>
        <span className="sky-rule" aria-hidden />
        <div className="clay-hero-actions">
          <Link to="/hr/claims/new" className="clay-btn clay-btn-dark clay-btn-go">
            <FileText className="size-[18px]" aria-hidden /> New claim <ArrowRight className="clay-go size-4" aria-hidden />
          </Link>
          <Link to="/hr/enrollment-forms" className="clay-btn clay-btn-white clay-btn-go">
            <FileSignature className="size-[18px]" aria-hidden /> Enrolment forms <ArrowRight className="clay-go size-4" aria-hidden />
          </Link>
        </div>
      </SkyStage>

      <section className="hr-overview-modules mx-auto flex w-full max-w-6xl flex-col gap-5 px-4 pb-8 pt-5" aria-label="HR services">
        {mfaSuggested && (
          <Card className="rounded-3xl border-warn/40 bg-[#fff8e8]">
            <CardHeader className="flex-row items-start gap-3 space-y-0">
              <ShieldAlert className="mt-0.5 size-5 shrink-0 text-warn" />
              <div>
                <CardTitle className="text-base">
                  Add two-factor authentication
                </CardTitle>
                <CardDescription>
                  Your company supports two-factor authentication. It's optional,
                  but adding it gives your account an extra layer of security.
                </CardDescription>
                <Button asChild size="sm" className="mt-3">
                  <Link to="/hr/security">Set up two-factor</Link>
                </Button>
              </div>
            </CardHeader>
          </Card>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          {[...MODULES.filter((m) => "href" in m), ...MODULES.filter((m) => !("href" in m))].map((m) => {
            const Icon = m.icon;
            if ("href" in m) {
              return (
                <Link
                  key={m.key}
                  to={m.href}
                  className="hr-module group rounded-3xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                >
                  <Card className={`h-full rounded-3xl transition-colors ${m.key === "claims" ? "bg-[#eef5ff]" : "bg-[#ebf8f0]"}`}>
                    <CardHeader>
                      <div className="flex items-center justify-between">
                        <Icon className="size-6 text-primary" aria-hidden />
                        <ArrowRight
                          className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5"
                          aria-hidden
                        />
                      </div>
                      <h2 className="mt-2 text-xl font-bold">{m.title}</h2>
                      <CardDescription>{m.description}</CardDescription>
                    </CardHeader>
                  </Card>
                </Link>
              );
            }
            return (
              <Card key={m.key}>
                <CardHeader className="pb-5">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <Icon className="size-5 text-primary" aria-hidden />
                      <h2 className="text-base font-semibold">{m.title}</h2>
                    </div>
                    <Badge variant="outline">Coming soon</Badge>
                  </div>
                </CardHeader>
              </Card>
            );
          })}
        </div>
      </section>
    </div>
  );
}
