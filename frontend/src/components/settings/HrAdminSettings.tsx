import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Copy, KeyRound, Loader2, Plus, RefreshCw } from "lucide-react";
import {
  useCreateHrAccount,
  useHrAccounts,
  useHrAuthPolicy,
  useRegenerateHrLoginId,
  useResetHrPassword,
  useSetHrAccountEnabled,
  useUpdateHrAuthPolicy,
  type HrAccountCreated,
  type HrAuthPolicy,
  type LoginSource,
} from "@/api/hrAdmin";
import { useMe } from "@/api/hooks";
import { formatError } from "@/lib/errors";
import { tenantSurfaceUrl } from "@/lib/tenant";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { InfoHint } from "@/components/ui/tooltip";

const ROLE_LABEL: Record<string, string> = {
  client_admin: "HR Administrator",
  client_hr: "HR Officer",
};

const LOGIN_SOURCES: { value: LoginSource; label: string }[] = [
  { value: "email", label: "Email address" },
  { value: "system_id", label: "System-generated ID" },
  { value: "staff_id", label: "Employee / Staff ID" },
];

function StatusBadge({ status }: { status: string }) {
  const variant =
    status === "active" ? "good" : status === "disabled" ? "error" : "warn";
  const label =
    status === "active" ? "Active" : status === "disabled" ? "Disabled" : "Invited";
  return <Badge variant={variant}>{label}</Badge>;
}

/** One-time reveal of a set-password link after create/reset. */
function SetPasswordReveal({ token, tenantSlug }: { token: string; tenantSlug?: string | null }) {
  // ABSOLUTE url: the HR surface lives on `{slug}.hr.<base>`, not on the broker
  // host this page is served from, so a bare path is unclickable once pasted
  // into an email — and the token is revealed only once.
  const link = tenantSurfaceUrl(
    "hr",
    tenantSlug,
    `/hr/set-password?token=${encodeURIComponent(token)}`,
  );
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      toast.success("Set-password link copied");
    } catch {
      toast.error("Couldn't copy — select and copy manually.");
    }
  };
  return (
    <div className="rounded-md border border-primary/30 bg-primary/5 p-3 text-sm">
      <p className="mb-1.5 font-medium text-foreground">
        Send this one-time set-password link to the HR admin
      </p>
      <p className="mb-2 text-xs text-muted-foreground">
        It opens on their company's HR subdomain and expires in 48 hours.
      </p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1 text-xs">
          {link}
        </code>
        <Button size="sm" variant="outline" onClick={copy}>
          <Copy className="size-3.5" /> Copy
        </Button>
      </div>
    </div>
  );
}

function SourceSelect({
  id,
  value,
  onChange,
}: {
  id: string;
  value: string;
  onChange: (v: LoginSource) => void;
}) {
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value as LoginSource)}
      className="flex h-9 w-full min-w-[13rem] rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
    >
      {LOGIN_SOURCES.map((s) => (
        <option key={s.value} value={s.value}>
          {s.label}
        </option>
      ))}
    </select>
  );
}

/** Broker-controlled sign-in settings for both surfaces (HR + employee portal):
 * the login username source and whether 2FA is available (self-enrol). */
function SignInSettingsCard({ clientId }: { clientId: string }) {
  const { data } = useHrAuthPolicy(clientId);
  const update = useUpdateHrAuthPolicy(clientId);
  const [draft, setDraft] = useState<HrAuthPolicy | null>(null);

  useEffect(() => {
    if (data) setDraft(data);
  }, [data]);

  if (!draft) {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading settings…
        </CardContent>
      </Card>
    );
  }

  const set = <K extends keyof HrAuthPolicy>(key: K, value: HrAuthPolicy[K]) =>
    setDraft({ ...draft, [key]: value });

  const save = async () => {
    try {
      await update.mutateAsync({
        mfa_hr_enabled: draft.mfa_hr_enabled,
        mfa_portal_enabled: draft.mfa_portal_enabled,
        hr_login_source: draft.hr_login_source,
        portal_login_source: draft.portal_login_source,
        breach_check_enabled: draft.breach_check_enabled,
        password_min_entropy: draft.password_min_entropy,
        password_rotation_days: draft.password_rotation_days,
        session_idle_minutes: draft.session_idle_minutes,
        session_absolute_hours: draft.session_absolute_hours,
      });
      toast.success("Sign-in settings updated");
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  const SaveButton = () => (
    <Button onClick={save} disabled={update.isPending} size="sm">
      {update.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
      Save
    </Button>
  );

  const signInSection = (
    surface: "hr" | "portal",
    title: string,
    who: string,
  ) => {
    const sourceKey = surface === "hr" ? "hr_login_source" : "portal_login_source";
    const mfaKey = surface === "hr" ? "mfa_hr_enabled" : "mfa_portal_enabled";
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">{title}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-1.5">
            <div className="flex items-center gap-1">
              <Label htmlFor={`src-${surface}`}>Login username</Label>
              <InfoHint>
                Which identifier {who.toLowerCase()} type to sign in. Members
                without an email should use a system-generated or employee ID.
              </InfoHint>
            </div>
            <SourceSelect
              id={`src-${surface}`}
              value={draft[sourceKey]}
              onChange={(v) => set(sourceKey, v)}
            />
          </div>
          <div className="flex items-center justify-between gap-4">
            <div>
              <Label className="text-sm">Two-factor authentication</Label>
              <p className="text-xs text-muted-foreground">
                When on, {who.toLowerCase()} can add an authenticator app
                (optional, set up by each person).
              </p>
            </div>
            <Switch
              checked={draft[mfaKey]}
              onCheckedChange={(v) => set(mfaKey, v)}
            />
          </div>
          <SaveButton />
        </CardContent>
      </Card>
    );
  };

  const numField = (
    key: "password_min_entropy" | "session_idle_minutes" | "session_absolute_hours",
    label: string,
    hint: string,
  ) => (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1">
        <Label>{label}</Label>
        <InfoHint>{hint}</InfoHint>
      </div>
      <Input
        type="number"
        min={0}
        className="h-9 w-40"
        value={draft[key]}
        onChange={(e) => set(key, Number(e.target.value))}
      />
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        {signInSection("hr", "HR sign-in", "HR admins")}
        {signInSection("portal", "Employee sign-in", "Employees")}
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Password & sessions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="flex items-center justify-between gap-4">
            <div>
              <Label className="text-sm">Breached-password check</Label>
              <p className="text-xs text-muted-foreground">
                Reject passwords found in known breaches (HaveIBeenPwned).
              </p>
            </div>
            <Switch
              checked={draft.breach_check_enabled}
              onCheckedChange={(v) => set("breach_check_enabled", v)}
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            {numField(
              "password_min_entropy",
              "Password strength floor (bits)",
              "Minimum estimated entropy for a password. 60 bits is a sensible default.",
            )}
            <div className="space-y-1.5">
              <div className="flex items-center gap-1">
                <Label>Password rotation (days)</Label>
                <InfoHint>
                  Force a password reset after this many days. Blank = no forced
                  rotation.
                </InfoHint>
              </div>
              <Input
                type="number"
                min={0}
                placeholder="No rotation"
                className="h-9 w-40"
                value={draft.password_rotation_days ?? ""}
                onChange={(e) =>
                  set(
                    "password_rotation_days",
                    e.target.value === "" ? null : Number(e.target.value),
                  )
                }
              />
            </div>
            {numField(
              "session_idle_minutes",
              "Idle timeout (minutes)",
              "Sign out after this long without activity (MAS TRM).",
            )}
            {numField(
              "session_absolute_hours",
              "Session lifetime (hours)",
              "Maximum session age before a fresh sign-in is required.",
            )}
          </div>
          <SaveButton />
        </CardContent>
      </Card>
    </div>
  );
}

function AccountsCard({ clientId }: { clientId: string }) {
  const { data: accounts = [], isLoading } = useHrAccounts(clientId);
  const create = useCreateHrAccount(clientId);
  const reset = useResetHrPassword(clientId);
  const regen = useRegenerateHrLoginId(clientId);
  const setEnabled = useSetHrAccountEnabled(clientId);

  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("client_hr");
  const [reveal, setReveal] = useState<HrAccountCreated | null>(null);

  const onCreate = async () => {
    const e = email.trim().toLowerCase();
    if (!e.includes("@")) {
      toast.error("Enter a valid email address.");
      return;
    }
    try {
      const created = await create.mutateAsync({
        email: e,
        display_name: name.trim() || undefined,
        role,
      });
      setReveal(created);
      setEmail("");
      setName("");
      toast.success("HR account created");
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  const onReset = async (userId: string) => {
    try {
      const out = await reset.mutateAsync(userId);
      setReveal(out);
      toast.success("New set-password link generated");
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  const onRegen = async (userId: string) => {
    try {
      await regen.mutateAsync(userId);
      toast.success("HR ID regenerated");
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  const onToggle = async (userId: string, enabled: boolean) => {
    try {
      await setEnabled.mutateAsync({ userId, enabled });
      toast.success(enabled ? "Account enabled" : "Account disabled");
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">HR admins</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <div className="flex-1 space-y-1.5">
            <Label htmlFor="hr-new-email">Email</Label>
            <Input
              id="hr-new-email"
              type="email"
              placeholder="name@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="flex-1 space-y-1.5">
            <Label htmlFor="hr-new-name">Name (optional)</Label>
            <Input
              id="hr-new-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="hr-new-role">Role</Label>
            <select
              id="hr-new-role"
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="flex h-9 w-full min-w-[9rem] rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              <option value="client_hr">HR Officer</option>
              <option value="client_admin">HR Administrator</option>
            </select>
          </div>
          <Button onClick={onCreate} disabled={create.isPending || !email.trim()}>
            {create.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Plus className="size-4" />
            )}
            Add HR admin
          </Button>
        </div>

        {reveal && (
          <SetPasswordReveal
            token={reveal.set_password_token}
            tenantSlug={reveal.tenant_slug}
          />
        )}

        {isLoading ? (
          <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading…
          </div>
        ) : accounts.length === 0 ? (
          <p className="py-4 text-sm text-muted-foreground">
            No HR admins yet — add the first above.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>HR ID</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>2FA</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {accounts.map((a) => (
                  <TableRow key={a.user_id}>
                    <TableCell className="font-medium">
                      {a.email}
                      {a.display_name && (
                        <span className="block text-xs text-muted-foreground">
                          {a.display_name}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {ROLE_LABEL[a.role] ?? a.role}
                    </TableCell>
                    <TableCell>
                      <code className="text-xs">{a.hr_login_id ?? "—"}</code>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={a.status} />
                    </TableCell>
                    <TableCell>
                      {a.mfa_enrolled ? (
                        <Badge variant="good">On</Badge>
                      ) : (
                        <span className="text-xs text-muted-foreground">Off</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          title="Send a new set-password link"
                          onClick={() => onReset(a.user_id)}
                          disabled={reset.isPending}
                        >
                          <KeyRound className="size-3.5" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          title="Regenerate HR ID"
                          onClick={() => onRegen(a.user_id)}
                          disabled={regen.isPending}
                        >
                          <RefreshCw className="size-3.5" />
                        </Button>
                        <Button
                          size="sm"
                          variant={a.status === "disabled" ? "outline" : "ghost"}
                          onClick={() =>
                            onToggle(a.user_id, a.status === "disabled")
                          }
                          disabled={setEnabled.isPending}
                        >
                          {a.status === "disabled" ? "Enable" : "Disable"}
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function HrAdminSettings() {
  const { data: me } = useMe();
  const clientId = me?.active_client_id ?? null;

  if (!clientId) {
    return (
      <p className="text-sm text-muted-foreground">
        Select a company to manage its sign-in settings.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <SignInSettingsCard clientId={clientId} />
      <AccountsCard clientId={clientId} />
    </div>
  );
}
