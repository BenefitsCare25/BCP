/** HR admin home — identity + entry points to the HR modules. The modules
 * themselves (employees, claims, policies) are the next build; this shell
 * establishes the authenticated surface and its navigation. */
import { Link } from "@tanstack/react-router";
import { ClipboardList, FileText, ShieldAlert, Users } from "lucide-react";
import { useHrMe } from "@/api/hr";
import { useHrSession } from "@/stores/hrSession";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const ROLE_LABEL: Record<string, string> = {
  client_admin: "HR Administrator",
  client_hr: "HR Officer",
};

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
  const loginFlag = useHrSession((s) => s.mfaEnrollmentRequired);
  const { data } = useHrMe();
  const identity = data ?? me;
  // The login flag is the immediate signal; fall back to the live identity
  // (2FA available but not yet confirmed) so the nudge survives a token refresh,
  // which re-seeds the session without the flag.
  const mfaNeeded =
    loginFlag ||
    (!!identity?.mfa_available && identity?.mfa_status !== "confirmed");

  return (
    <div className="space-y-8">
      <div>
        <p className="text-sm text-muted-foreground">
          {identity?.company_name ?? "Your company"}
        </p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-foreground">
          Welcome{identity?.display_name ? `, ${identity.display_name}` : ""}
        </h1>
        {identity?.role && (
          <Badge variant="info" className="mt-2">
            {ROLE_LABEL[identity.role] ?? identity.role}
          </Badge>
        )}
      </div>

      {mfaNeeded && (
        <Card className="border-warn/40 bg-warn/5">
          <CardHeader className="flex-row items-start gap-3 space-y-0">
            <ShieldAlert className="mt-0.5 size-5 shrink-0 text-warn" />
            <div>
              <CardTitle className="text-base">
                Two-factor authentication required
              </CardTitle>
              <CardDescription>
                Your company requires two-factor authentication. Set it up now to
                secure your access.
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
          return (
            <Card key={m.key} className="opacity-70">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <Icon className="size-6 text-primary" />
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
