import { useId, useState } from "react";
import { toast } from "sonner";
import { useAdminClients } from "@/api/hooks";
import { useFirmProfile } from "@/api/firm";
import {
  type CompanyBrand,
  type FirmBrand,
  brandErrorMessage,
  hasCompanyOverride,
  isBrandRevisionStale,
  useCompanyBrand,
  useDeleteCompanyBrand,
  useFirmBrand,
  useUpdateCompanyBrand,
} from "@/api/brand";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Separator } from "@/components/ui/separator";
import { ListError, ListLoading } from "@/components/platform/QueryStates";
import { errorCode } from "@/lib/errors";
import { BrandAssetsSection } from "./BrandAssetsSection";
import { BrandFormFooter, ColorFields, ContactFields, IdentityFields } from "./BrandFieldsets";
import { BUILT_IN_EXTRA, type Draft, fieldRefusal, fieldsOf, useBrandDraft, validateDraft } from "./brandForm";

/** Per-company overrides of the firm brand, shown on that company's HR and
 *  employee portals. Blank fields use the firm's brand. The From address, the
 *  e-card prefix and the platform attribution are the firm's alone. */
export function CompanyBrandCard({ readOnly }: { readOnly: boolean }) {
  const id = useId();
  const firm = useFirmProfile();
  const clients = useAdminClients();
  const [clientId, setClientId] = useState("");
  const companies = (clients.data ?? []).filter((c) => !firm.data || c.broker_firm_id === firm.data.id);
  const chosen = companies.find((c) => c.id === clientId) ?? null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Company brand overrides</CardTitle>
        <CardDescription>
          A company&apos;s HR and employee portals can show its own name, colours and images instead of the
          firm&apos;s. Fields left blank use the firm&apos;s brand.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {clients.isPending ? (
          <ListLoading label="Loading companies…" />
        ) : clients.isError ? (
          <ListError what="companies" error={clients.error} onRetry={() => void clients.refetch()} />
        ) : companies.length === 0 ? (
          <p className="text-sm text-muted-foreground">Your firm has no companies yet.</p>
        ) : (
          <div className="flex max-w-sm flex-col gap-1.5">
            <Label htmlFor={`${id}-company`}>Company</Label>
            <NativeSelect
              id={`${id}-company`}
              value={clientId}
              className="h-9"
              onChange={(event) => setClientId(event.target.value)}
            >
              <option value="">Choose a company</option>
              {companies.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </NativeSelect>
          </div>
        )}
        {chosen && <CompanyBrandEditor key={chosen.id} clientId={chosen.id} name={chosen.name} readOnly={readOnly} />}
      </CardContent>
    </Card>
  );
}

function CompanyBrandEditor({ clientId, name, readOnly }: { clientId: string; name: string; readOnly: boolean }) {
  const query = useCompanyBrand(clientId);
  const firmBrand = useFirmBrand();
  if (query.isPending || firmBrand.isPending) return <ListLoading label={`Loading ${name}'s brand…`} />;
  if (query.isError) {
    return <ListError what={`${name}'s brand`} error={query.error} onRetry={() => void query.refetch()} />;
  }
  if (firmBrand.isError) {
    return <ListError what="the firm brand" error={firmBrand.error} onRetry={() => void firmBrand.refetch()} />;
  }
  return (
    <CompanyBrandForm
      clientId={clientId}
      name={name}
      data={query.data}
      firm={firmBrand.data}
      readOnly={readOnly}
      onRefetch={() => void query.refetch()}
    />
  );
}

function CompanyBrandForm({
  clientId,
  name,
  data,
  firm,
  readOnly,
  onRefetch,
}: {
  clientId: string;
  name: string;
  data: CompanyBrand;
  firm: FirmBrand;
  readOnly: boolean;
  onRefetch: () => void;
}) {
  const id = useId();
  const update = useUpdateCompanyBrand(clientId);
  const remove = useDeleteCompanyBrand(clientId);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const { draft, setDraft, revision, dirty, adoptSaved, reset } = useBrandDraft(data.settings, false);
  const errors = validateDraft(draft, false, {
    product_name: firm.settings.product_name,
    support_email: firm.settings.support_email,
  });
  const valid = Object.keys(errors).length === 0;
  const disabled = readOnly || update.isPending || remove.isPending;
  const hasOverride = hasCompanyOverride(data);
  const refusedField = update.isError ? fieldRefusal(errorCode(update.error)) : null;
  const refusal = update.isError ? brandErrorMessage(update.error) : null;
  const shownErrors = refusedField ? { ...errors, [refusedField]: refusal ?? undefined } : errors;

  const change = (next: Partial<Draft>) => {
    if (update.isError) update.reset();
    setDraft((current) => ({ ...current, ...next }));
  };

  const save = () => {
    if (readOnly || !valid || !dirty || update.isPending) return;
    update.mutate(
      { ...fieldsOf(draft), revision },
      {
        onSuccess: () => {
          adoptSaved();
          toast.success(`Brand saved for ${name}`);
        },
      },
    );
  };

  const fieldProps = {
    id,
    draft,
    errors: shownErrors,
    disabled,
    inherited: data.inherited,
    inheritedExtra: {
      email_sender_name: firm.settings.email_sender_name ?? BUILT_IN_EXTRA.email_sender_name,
    },
    onChange: change,
  };

  return (
    <div className="space-y-6 rounded-md border border-border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h4 className="text-sm font-semibold text-foreground">{name}</h4>
          <Badge variant={hasOverride ? "primary" : "default"}>{hasOverride ? "Own brand" : "Uses the firm brand"}</Badge>
        </div>
        {hasOverride && !readOnly && (
          <Button type="button" variant="destructiveOutline" size="sm" disabled={disabled} onClick={() => setConfirmRemove(true)}>
            Remove override
          </Button>
        )}
      </div>
      <form
        noValidate
        className="space-y-6"
        onSubmit={(event) => {
          event.preventDefault();
          save();
        }}
      >
        <IdentityFields {...fieldProps} />
        <ColorFields {...fieldProps} />
        <ContactFields {...fieldProps} />
        {!readOnly && (
          <BrandFormFooter
            saveLabel={`Save ${name}'s brand`}
            dirty={dirty}
            canSave={dirty && valid}
            pending={update.isPending}
            stale={isBrandRevisionStale(update.error)}
            error={refusedField ? null : refusal}
            onDiscard={() => {
              reset();
              update.reset();
            }}
            onReload={() => {
              adoptSaved();
              update.reset();
              onRefetch();
            }}
          />
        )}
      </form>
      <Separator />
      <BrandAssetsSection
        scope={clientId}
        assets={data.settings.assets}
        effective={data.effective}
        inheritedFrom="the firm"
        readOnly={readOnly}
      />
      <AlertDialog
        open={confirmRemove}
        onOpenChange={(open) => !open && !remove.isPending && setConfirmRemove(false)}
        title={`Remove ${name}'s brand override?`}
        description={`${name}'s HR and employee portals will show the firm's brand again, and its own name, colours and images are deleted.`}
        confirmLabel="Remove override"
        loading={remove.isPending}
        onConfirm={() =>
          remove.mutate(data.settings.revision, {
            onSuccess: () => {
              adoptSaved();
              setConfirmRemove(false);
              toast.success(`${name} now uses the firm brand`);
            },
            onError: (error) => {
              setConfirmRemove(false);
              toast.error(brandErrorMessage(error));
            },
          })
        }
      />
    </div>
  );
}
