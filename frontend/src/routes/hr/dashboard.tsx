/** HR admin home — identity and entry points to operational modules. Claims
 * are live; the remaining modules stay visibly unavailable until ready. */
import { Link } from "@tanstack/react-router";
import { ArrowRight, ClipboardList, FileText, ShieldAlert, Users } from "lucide-react";
import { useHrMe } from "@/api/hr";
import { useHrSession } from "@/stores/hrSession";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

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
    description: "Track and manage employee claims for your organisation.",
    icon: ClipboardList,
    href: "/hr/claims" as const,
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
  // Optional-2FA nudge, derived from the live identity: the company has 2FA
  // available and this person hasn't confirmed enrolment yet. Deriving it (not
  // storing a session flag) means it stays correct across token refresh and
  // updates the instant enrolment completes.
  const mfaSuggested =
    !!identity?.mfa_available && identity?.mfa_status !== "confirmed";

  return (
    <div className="space-y-5">
      {mfaSuggested && (
        <Card className="border-warn/40 bg-warn/5">
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

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {MODULES.map((m) => {
          const Icon = m.icon;
          if ("href" in m) {
            return (
              <Link
                key={m.key}
                to={m.href}
                className="group rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
              >
                <Card className="h-full transition-colors group-hover:bg-muted/50">
                  <CardHeader>
                    <div className="flex items-center justify-between">
                      <Icon className="size-6 text-primary" aria-hidden />
                      <ArrowRight
                        className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5"
                        aria-hidden
                      />
                    </div>
                    <CardTitle className="mt-3 text-lg">{m.title}</CardTitle>
                    <CardDescription>{m.description}</CardDescription>
                  </CardHeader>
                </Card>
              </Link>
            );
          }
          return (
            <Card key={m.key} className="opacity-70">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <Icon className="size-6 text-primary" aria-hidden />
                  <Badge variant="outline">Coming soon</Badge>
                </div>
                <CardTitle className="mt-3 text-lg">{m.title}</CardTitle>
                <CardDescription>{m.description}</CardDescription>
              </CardHeader>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
