import { Link } from "@tanstack/react-router";
import { Building2, Home } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { isBrokerAdminRole } from "@/lib/roles";

/**
 * Shown in place of a company page when the caller can reach no company at
 * all. Rendering the page anyway sent every company-scoped read to the server
 * with nothing to scope it to, and each refusal re-prompted a picker that had
 * nothing to offer.
 */
export function NoCompanyNotice({ role }: { role: string | undefined }) {
  const canCreate = isBrokerAdminRole(role);
  return (
    <div className="mx-auto max-w-lg py-8">
      <Card className="p-6">
        <div className="flex items-center gap-2">
          <Building2 className="size-5 text-primary" aria-hidden="true" />
          <h1 className="text-base font-semibold text-foreground">
            {canCreate ? "No companies yet" : "No company access yet"}
          </h1>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {canCreate
            ? "This page works on one company, and none has been set up. Create the first company under Access & Companies, then return here."
            : "This page works on one company, and your account cannot reach any yet. Ask a broker administrator to give you access."}
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          {canCreate && (
            <Button asChild size="sm">
              <Link to="/firm/access">
                <Building2 className="size-4" aria-hidden="true" />
                Open Access &amp; Companies
              </Link>
            </Button>
          )}
          <Button asChild size="sm" variant="outline">
            <Link to="/home">
              <Home className="size-4" aria-hidden="true" />
              Back to Home
            </Link>
          </Button>
        </div>
      </Card>
    </div>
  );
}
