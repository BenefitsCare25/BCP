import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useMe } from "@/api/hooks";
import { SystemAdminOnly } from "@/components/auth/SystemAdminOnly";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { AIProviderPage } from "@/routes/configuration/ai-provider";
import { AIUsageTile } from "@/components/schema/AIUsageTile";
import { PlatformAIProviderCard } from "@/components/configuration/PlatformAIProviderCard";
import { PlatformAILimitsCard } from "@/components/configuration/PlatformAILimitsCard";
import { useSession } from "@/stores/session";
import { isBrokerStaffRole } from "@/lib/roles";
import { PolicyLibrary } from "./policies";

export function AISettingsPage() {
  const { data: me, isPending, isError, refetch } = useMe();
  const search = useSearch({ strict: false }) as { tab?: string };
  const navigate = useNavigate();
  const activeClientId = useSession((state) => state.activeClientId);
  const tab = ["provider", "policies", "usage"].includes(search.tab ?? "")
    ? search.tab!
    : "provider";
  if (isPending) return <p role="status">Loading AI settings…</p>;
  if (isError)
    return (
      <div role="alert">
        <p>Could not check your access.</p>
        <Button type="button" onClick={() => void refetch()}>
          Try again
        </Button>
      </div>
    );
  if (!me || !isBrokerStaffRole(me.role))
    return (
      <div>
        <h1 className="text-2xl font-semibold">AI Settings</h1>
        <p className="mt-3">Broker access is required.</p>
      </div>
    );
  return (
    <div className="mx-auto max-w-screen-2xl">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">AI Settings</h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
          Manage AI connections, find the current policies and understand who
          can change what.
        </p>
      </header>
      <Tabs
        value={tab}
        onValueChange={(value) =>
          void navigate({ to: "/settings/ai", search: { tab: value } })
        }
      >
        <TabsList
          aria-label="AI settings sections"
          className="mb-6 flex w-full max-w-full justify-start gap-4 overflow-x-auto sm:gap-6"
        >
          <TabsTrigger value="provider">Provider &amp; usage</TabsTrigger>
          <TabsTrigger value="policies">Policies</TabsTrigger>
          <TabsTrigger value="usage">AI use &amp; access</TabsTrigger>
        </TabsList>
        <TabsContent value="provider" className="mt-0">
          {!activeClientId ? (
            <div className="space-y-5">
              <SystemAdminOnly>
                <PlatformAIProviderCard />
                <PlatformAILimitsCard />
              </SystemAdminOnly>
              <p className="text-sm text-muted-foreground">
                Select a company in the top bar to view its AI usage and company
                settings. Platform policies are available in the Policies tab.
              </p>
            </div>
          ) : me.role === "broker_viewer" ? (
            <div className="space-y-5">
              <p className="text-sm text-muted-foreground">
                Your access is read-only. A broker administrator manages company
                AI settings; a system administrator manages the shared provider.
              </p>
              <AIUsageTile />
            </div>
          ) : (
            <AIProviderPage />
          )}
        </TabsContent>
        <TabsContent value="policies" className="mt-0">
          <PolicyLibrary />
        </TabsContent>
        <TabsContent value="usage" className="mt-0">
          <AIUseAndAccess />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function AIUseAndAccess() {
  return (
    <div className="space-y-8">
      <section aria-labelledby="ai-responsibilities">
        <h2 id="ai-responsibilities" className="text-lg font-semibold">
          Access follows your existing role
        </h2>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          No additional owner assignments are needed. Document records identify
          the person who uploaded or published each version.
        </p>
        <dl className="mt-5 divide-y divide-border rounded-lg border border-border bg-card">
          {[
            [
              "System administrator",
              "system_admin",
              "Manages the shared provider, platform limits and policy documents. Can also manage company overrides.",
            ],
            [
              "Firm administrator",
              "firm_admin",
              "Everything a broker administrator can do, plus the firm’s users, web addresses and removal of saved data, including clearing a company’s AI key.",
            ],
            [
              "Broker administrator",
              "broker_admin",
              "Manages the selected company’s AI key and budget. Reads published platform policies and manages operational claim settings.",
            ],
            [
              "Broker viewer",
              "broker_viewer",
              "Reads published policies and available usage information. Cannot change settings, upload documents or publish policies.",
            ],
          ].map(([name, role, description]) => (
            <div
              key={role}
              className="grid gap-3 p-5 md:grid-cols-[220px_minmax(0,1fr)]"
            >
              <dt>
                <span className="block font-medium">{name}</span>
                <span className="mt-1 block text-xs text-muted-foreground">
                  {role}
                </span>
              </dt>
              <dd className="text-sm leading-relaxed text-muted-foreground">
                {description}
              </dd>
            </div>
          ))}
        </dl>
      </section>
      <section aria-labelledby="ai-use">
        <h2 id="ai-use" className="text-lg font-semibold">
          Where AI is used
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          These are the workflows supported by the application. Availability
          depends on provider configuration and each workflow’s settings.
        </p>
        <ul className="mt-5 divide-y divide-border rounded-lg border border-border bg-card">
          <li className="p-5">
            <h3 className="font-medium">Claim review</h3>
            <p className="mt-2 max-w-3xl text-sm leading-relaxed text-muted-foreground">
              AI checks submitted evidence and returns review findings. Claim
              decisions and review rules remain in Claims Review.
            </p>
            <Link
              to="/claims/review"
              className="mt-2 inline-flex min-h-11 items-center text-sm text-primary underline underline-offset-4"
            >
              Open Claims Review
            </Link>
          </li>
          <li className="p-5">
            <h3 className="font-medium">Member claim autofill</h3>
            <p className="mt-2 max-w-3xl text-sm leading-relaxed text-muted-foreground">
              AI suggests details from uploaded claim documents. Members check
              and edit the details in the member claim form.
            </p>
          </li>
          <li className="p-5">
            <h3 className="font-medium">
              Document extraction and setup assistance
            </h3>
            <p className="mt-2 max-w-3xl text-sm leading-relaxed text-muted-foreground">
              AI assists with extracting placement-slip information and
              suggesting configuration. Brokers check the source document and
              resulting setup in the relevant workflow.
            </p>
          </li>
        </ul>
      </section>
    </div>
  );
}
