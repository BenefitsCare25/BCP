import type { ReactNode } from "react";
import type { PublicBrand } from "@/api/public";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { BrandColorField } from "./BrandColorField";
import { TEXT_FIELDS, type Draft, type Errors, type TextKey } from "./brandForm";

type FieldProps = {
  id: string;
  draft: Draft;
  errors: Errors;
  disabled: boolean;
  /** What a blank field resolves to, as placeholders. */
  inherited: PublicBrand;
  /** Inherited values the public brand does not carry. */
  inheritedExtra: { email_sender_name: string; card_prefix?: string };
  onChange: (next: Partial<Draft>) => void;
};

export function BrandTextField({
  id,
  name,
  draft,
  errors,
  disabled,
  placeholder,
  onChange,
  after,
}: {
  id: string;
  name: TextKey;
  draft: Draft;
  errors: Errors;
  disabled: boolean;
  placeholder?: string | null;
  onChange: (next: Partial<Draft>) => void;
  /** Status shown under the help, such as the From address verification. */
  after?: ReactNode;
}) {
  const spec = TEXT_FIELDS[name];
  const fieldId = `${id}-${name}`;
  const error = errors[name];
  const describedBy = [spec.help ? `${fieldId}-help` : "", error ? `${fieldId}-error` : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={fieldId}>{spec.label}</Label>
      <Input
        id={fieldId}
        type={spec.type}
        value={draft[name]}
        maxLength={spec.max}
        placeholder={placeholder ? `Inherits: ${placeholder}` : undefined}
        autoComplete="off"
        spellCheck={spec.type === "text" && name !== "card_prefix"}
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className={name === "card_prefix" ? "font-mono" : undefined}
        onChange={(event) =>
          onChange({ [name]: spec.transform ? spec.transform(event.target.value) : event.target.value })
        }
      />
      {spec.help && (
        <p id={`${fieldId}-help`} className="text-xs text-muted-foreground">
          {spec.help}
        </p>
      )}
      {after}
      {error && (
        <p id={`${fieldId}-error`} role="alert" className="text-xs text-error">
          {error}
        </p>
      )}
    </div>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="space-y-3">
      <legend className="text-sm font-medium text-foreground">{title}</legend>
      <div className="grid gap-4 sm:grid-cols-2">{children}</div>
    </fieldset>
  );
}

/** Product name and short name. */
export function IdentityFields(props: FieldProps) {
  const common = { id: props.id, draft: props.draft, errors: props.errors, disabled: props.disabled, onChange: props.onChange };
  return (
    <Group title="Name">
      <BrandTextField {...common} name="product_name" placeholder={props.inherited.product_name} />
      <BrandTextField {...common} name="short_name" placeholder={props.inherited.short_name} />
    </Group>
  );
}

/** Primary and accent colours with live contrast checks. */
export function ColorFields(props: FieldProps) {
  return (
    <Group title="Colours">
      <BrandColorField
        id={`${props.id}-primary`}
        label="Primary colour"
        help="Main buttons, links and keyboard focus."
        value={props.draft.primary_color}
        inherited={props.inherited.primary_color}
        error={props.errors.primary_color}
        disabled={props.disabled}
        onChange={(primary_color) => props.onChange({ primary_color })}
      />
      <BrandColorField
        id={`${props.id}-accent`}
        label="Accent colour"
        help="Soft highlights: selected items and badges."
        value={props.draft.accent_color}
        inherited={props.inherited.accent_color}
        error={props.errors.accent_color}
        disabled={props.disabled}
        onChange={(accent_color) => props.onChange({ accent_color })}
      />
    </Group>
  );
}

/** Support contact, email sender name and reply-to. The firm form adds its
 *  From address (`fromAddress`) and the e-card prefix (`cardPrefix`), which
 *  are the firm's alone. */
export function ContactFields(props: FieldProps & { fromAddress?: ReactNode; cardPrefix?: boolean }) {
  const common = { id: props.id, draft: props.draft, errors: props.errors, disabled: props.disabled, onChange: props.onChange };
  return (
    <>
      <Group title="Support contact">
        <BrandTextField {...common} name="support_email" placeholder={props.inherited.support_email} />
        <BrandTextField {...common} name="support_phone" placeholder={props.inherited.support_phone} />
      </Group>
      <Group title="System email">
        <BrandTextField {...common} name="email_sender_name" placeholder={props.inheritedExtra.email_sender_name} />
        <BrandTextField {...common} name="email_reply_to" />
        {props.fromAddress}
      </Group>
      {props.cardPrefix && (
        <Group title="E-cards">
          <BrandTextField {...common} name="card_prefix" placeholder={props.inheritedExtra.card_prefix} />
        </Group>
      )}
    </>
  );
}

/** Save / discard, the stale-revision reload prompt and the server's refusal. */
export function BrandFormFooter({
  saveLabel,
  dirty,
  canSave,
  pending,
  stale,
  error,
  onDiscard,
  onReload,
}: {
  saveLabel: string;
  dirty: boolean;
  canSave: boolean;
  pending: boolean;
  stale: boolean;
  /** Any other refusal, already formatted. */
  error: string | null;
  onDiscard: () => void;
  onReload: () => void;
}) {
  return (
    <div className="space-y-3">
      {stale && (
        <div role="alert" className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-warn-soft p-3 text-sm text-warn">
          <p className="min-w-0 flex-1">
            Someone else changed this brand since you opened it. Reload to see the current settings; your unsaved
            changes here will be discarded.
          </p>
          <Button type="button" size="sm" variant="outline" onClick={onReload}>
            Reload
          </Button>
        </div>
      )}
      {error && !stale && (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" loading={pending} disabled={!canSave || pending}>
          {saveLabel}
        </Button>
        {dirty && (
          <Button type="button" variant="ghost" disabled={pending} onClick={onDiscard}>
            Discard changes
          </Button>
        )}
      </div>
    </div>
  );
}

export type { FieldProps as BrandFieldProps };
