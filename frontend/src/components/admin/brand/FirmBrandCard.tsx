import { useId } from "react";
import { toast } from "sonner";
import {
  type FirmBrand,
  brandErrorMessage,
  isBrandRevisionStale,
  useFirmBrand,
  useUpdateFirmBrand,
} from "@/api/brand";
import { DEFAULT_BRAND } from "@/api/public";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { ListError, ListLoading } from "@/components/platform/QueryStates";
import { errorCode } from "@/lib/errors";
import { fmtDateTime } from "@/lib/format";
import { BrandAssetsSection } from "./BrandAssetsSection";
import { BrandFormFooter, BrandTextField, ColorFields, ContactFields, IdentityFields } from "./BrandFieldsets";
import { BUILT_IN_EXTRA, type Draft, fieldRefusal, fieldsOf, useBrandDraft, validateDraft } from "./brandForm";

/** The firm's white-label brand: name, colours, images, support contact,
 *  system email identity, e-card prefix and the platform attribution. A firm
 *  admin edits it; anyone else who can open the firm console reads it. */
export function FirmBrandCard({ readOnly }: { readOnly: boolean }) {
  const query = useFirmBrand();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Brand</CardTitle>
        <CardDescription>
          How your staff site, HR and employee portals, system emails and e-cards look. Blank fields use the
          platform default. Companies can override it below.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <ListLoading label="Loading brand…" />
        ) : query.isError ? (
          <ListError what="the brand" error={query.error} onRetry={() => void query.refetch()} />
        ) : (
          <FirmBrandForm data={query.data} readOnly={readOnly} onRefetch={() => void query.refetch()} />
        )}
      </CardContent>
    </Card>
  );
}

function FirmBrandForm({
  data,
  readOnly,
  onRefetch,
}: {
  data: FirmBrand;
  readOnly: boolean;
  onRefetch: () => void;
}) {
  const id = useId();
  const update = useUpdateFirmBrand();
  const { draft, setDraft, revision, dirty, adoptSaved, reset } = useBrandDraft(data.settings, true);
  const errors = validateDraft(draft, true);
  const valid = Object.keys(errors).length === 0;
  const stale = isBrandRevisionStale(update.error);
  const refusedField = update.isError ? fieldRefusal(errorCode(update.error)) : null;
  const refusal = update.isError ? brandErrorMessage(update.error) : null;
  const shownErrors = refusedField ? { ...errors, [refusedField]: refusal ?? undefined } : errors;
  const entitled = data.entitlements.allow_hide_attribution;
  const disabled = readOnly || update.isPending;

  const change = (next: Partial<Draft>) => {
    if (update.isError) update.reset();
    setDraft((current) => ({ ...current, ...next }));
  };

  const save = () => {
    if (readOnly || !valid || !dirty || update.isPending) return;
    const stored = data.settings.show_platform_attribution;
    // Unchanged (null reads as "shown") is sent back as stored; only an
    // entitled firm can change it.
    const attribution =
      entitled && draft.show_platform_attribution !== (stored !== false) ? draft.show_platform_attribution : stored;
    update.mutate(
      {
        ...fieldsOf(draft),
        email_from_address: draft.email_from_address.trim() || null,
        card_prefix: draft.card_prefix.trim().toUpperCase() || null,
        show_platform_attribution: attribution,
        revision,
      },
      {
        onSuccess: () => {
          adoptSaved();
          toast.success("Brand saved");
        },
      },
    );
  };

  const fieldProps = {
    id,
    draft,
    errors: shownErrors,
    disabled,
    inherited: DEFAULT_BRAND,
    inheritedExtra: BUILT_IN_EXTRA,
    onChange: change,
  };

  return (
    <div className="space-y-6">
      {readOnly && (
        <p className="text-sm text-muted-foreground">Only a firm admin can change the brand.</p>
      )}
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
        <ContactFields
          {...fieldProps}
          cardPrefix
          fromAddress={
            <BrandTextField
              id={id}
              name="email_from_address"
              draft={draft}
              errors={shownErrors}
              disabled={disabled}
              onChange={change}
              after={<FromAddressStatus data={data} draftAddress={draft.email_from_address} />}
            />
          }
        />
        <AttributionToggle
          id={`${id}-attribution`}
          platformName={data.effective.platform_name}
          entitled={entitled}
          checked={entitled ? draft.show_platform_attribution : true}
          disabled={disabled || !entitled}
          onChange={(show_platform_attribution) => change({ show_platform_attribution })}
        />
        {!readOnly && (
          <BrandFormFooter
            saveLabel="Save brand"
            dirty={dirty}
            canSave={dirty && valid}
            pending={update.isPending}
            stale={stale}
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
        scope="firm"
        assets={data.settings.assets}
        effective={data.effective}
        inheritedFrom="the platform default"
        readOnly={readOnly}
      />
    </div>
  );
}

/** Whether the saved From address is in use yet. A new address clears the
 *  verification when saved. */
function FromAddressStatus({ data, draftAddress }: { data: FirmBrand; draftAddress: string }) {
  const saved = (data.settings.email_from_address ?? "").trim().toLowerCase();
  const typed = draftAddress.trim().toLowerCase();
  if (typed !== saved) {
    return typed ? (
      <p className="text-xs text-muted-foreground">
        A new address waits for the platform to verify it before emails use it.
      </p>
    ) : null;
  }
  if (!saved) return null;
  const verifiedAt = data.email_from.verified_at;
  return (
    <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      {verifiedAt ? (
        <>
          <Badge variant="good">Verified</Badge>
          <span>on {fmtDateTime(verifiedAt)}</span>
        </>
      ) : (
        <Badge variant="warn">Waiting for verification by the platform</Badge>
      )}
    </p>
  );
}

function AttributionToggle({
  id,
  platformName,
  entitled,
  checked,
  disabled,
  onChange,
}: {
  id: string;
  platformName: string;
  entitled: boolean;
  checked: boolean;
  disabled: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <fieldset className="space-y-3">
      <legend className="text-sm font-medium text-foreground">Platform attribution</legend>
      <div className="flex items-start justify-between gap-4">
        <div>
          <Label htmlFor={id} className="text-sm">
            Show &ldquo;Powered by {platformName}&rdquo;
          </Label>
          <p id={`${id}-help`} className="max-w-xl text-xs text-muted-foreground">
            {entitled
              ? "A quiet line in the footer of your sign-in pages and the HR and employee portals."
              : `Always shown for your firm. The ${platformName} platform team can allow hiding it.`}
          </p>
        </div>
        <Switch
          id={id}
          aria-describedby={`${id}-help`}
          checked={checked}
          disabled={disabled}
          onCheckedChange={onChange}
        />
      </div>
    </fieldset>
  );
}
