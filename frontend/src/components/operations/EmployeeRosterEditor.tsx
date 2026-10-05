import { useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useBlocker } from "@tanstack/react-router";
import { Check, Pencil, Save } from "lucide-react";
import { toast } from "sonner";
import { useMe, useUpdateEmployee } from "@/api/hooks";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { formatError } from "@/lib/errors";
import type { Employee, EmployeeRosterField } from "@/types";
import { RosterFieldInput, rosterDraftValue, rosterFieldType, validRosterDate } from "./RosterFieldInput";

type EditorField = EmployeeRosterField & { draftKey: string; objectKey?: string };

function valueFor(employee: Employee, field: EditorField): unknown {
  const value = employee.attribute_values[field.attribute_id];
  return field.objectKey && value && typeof value === "object"
    ? (value as Record<string, unknown>)[field.objectKey] : field.objectKey ? null : value;
}

function editValue(value: unknown): string {
  if (value == null) return "";
  return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
}

function displayValue(value: unknown): string {
  if (value == null || value === "") return "Not provided";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return editValue(value);
}

function parseValue(value: string, type: string, label: string): unknown {
  if (!value.trim()) return null;
  if (["decimal", "number", "integer", "float"].includes(type)) {
    const number = Number(value);
    if (!Number.isFinite(number) || (type === "integer" && !Number.isInteger(number))) {
      throw new Error(`${label}: enter a valid ${type === "integer" ? "whole number" : "number"}.`);
    }
    return number;
  }
  if (type === "date" && !validRosterDate(value)) throw new Error(`${label}: choose a valid date.`);
  if (type === "boolean") {
    if (!["true", "false"].includes(value)) throw new Error(`${label}: choose Yes or No.`);
    return value === "true";
  }
  if (type === "json") {
    try {
      const parsed: unknown = JSON.parse(value);
      if (parsed == null || typeof parsed !== "object") throw new Error();
      return parsed;
    } catch {
      throw new Error(`${label}: enter a valid JSON object or list.`);
    }
  }
  // Text identifiers, including leading zeros, must stay text.
  return value;
}

export function EmployeeRosterEditor({ employee, actionContainer, disabled = false, onEditingChange }: {
  employee: Employee; actionContainer?: HTMLElement | null; disabled?: boolean;
  onEditingChange?: (editing: boolean) => void;
}) {
  const { data: me } = useMe();
  const update = useUpdateEmployee();
  const [editing, setEditing] = useState(false);
  const [baseline, setBaseline] = useState(employee);
  const [name, setName] = useState("");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [badInputs, setBadInputs] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [cancelPrompt, setCancelPrompt] = useState(false);
  const editButton = useRef<HTMLButtonElement>(null);
  const editorSection = useRef<HTMLElement>(null);
  const canEdit = Boolean(me && me.role !== "broker_viewer");
  const current = editing ? baseline : employee;
  const fields = useMemo(() => {
    const byId = new Map(
      (current.roster_fields ?? []).map((field) => [field.attribute_id, field]),
    );
    for (const key of Object.keys(current.attribute_values)) {
      if (!byId.has(key)) byId.set(key, {
        attribute_id: key, display_name: key.replace(/_/g, " "), data_type: "string",
      });
    }
    byId.delete("employee_name");
    byId.delete("staff_id");
    return [...byId.values()].flatMap<EditorField>((field) => {
      if (field.attribute_id === "insurer_member_ids") {
        const value = current.attribute_values[field.attribute_id];
        const insurers = new Set([
          ...Object.keys(value && typeof value === "object" ? value : {}),
          ...(field.object_keys ?? []),
        ]);
        if (insurers.size > 0) return [...insurers].map((insurer) => ({
          ...field, draftKey: `${field.attribute_id}.${insurer}`, objectKey: insurer,
          display_name: `${insurer} Member ID`, data_type: "string",
        }));
      }
      return [{ ...field, draftKey: field.attribute_id }];
    });
  }, [current]);
  const changedFields = fields.filter(
    (field) => (draft[field.draftKey] ?? "") !== rosterDraftValue(field, valueFor(baseline, field)),
  );
  const nameChanged = name !== (baseline.employee_name ?? "");
  const changedKeys = new Set([...changedFields.map((field) => field.draftKey), ...badInputs]);
  const changeCount = changedKeys.size + Number(nameChanged);
  const dirty = editing && changeCount > 0;
  const blocker = useBlocker({
    shouldBlockFn: ({ current: from, next }) =>
      (dirty || update.isPending) &&
      (from.pathname !== next.pathname || JSON.stringify(from.search) !== JSON.stringify(next.search)),
    enableBeforeUnload: () => dirty || update.isPending,
    disabled: !dirty && !update.isPending,
    withResolver: true,
  });
  const leaving = blocker.status === "blocked";
  const blockerRef = useRef(blocker);
  blockerRef.current = blocker;

  const startEditing = () => {
    onEditingChange?.(true);
    setBaseline(employee);
    setName(employee.employee_name ?? "");
    setDraft(Object.fromEntries(fields.map((field) => [
      field.draftKey, rosterDraftValue(field, valueFor(employee, field)),
    ])));
    setError(null);
    setBadInputs(new Set());
    setSaved(false);
    setEditing(true);
  };
  const finishEditing = () => {
    onEditingChange?.(false);
    setEditing(false);
    setCancelPrompt(false);
    setError(null);
    setBadInputs(new Set());
    requestAnimationFrame(() => editButton.current?.focus());
  };
  const discard = () => {
    finishEditing();
    if (blocker.status === "blocked") blocker.proceed();
  };
  const save = async () => {
    if (update.isPending || !dirty) return;
    setError(null);
    try {
      // Validate only edited fields. A legacy invalid value on another field
      // must not be erased or prevent an unrelated correction from being saved.
      for (const field of fields.filter((candidate) => changedKeys.has(candidate.draftKey))) {
        const wrapper = [...(editorSection.current?.querySelectorAll<HTMLElement>("[data-roster-field]") ?? [])]
          .find((element) => element.dataset.rosterField === field.draftKey);
        const control = wrapper?.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("input,select,textarea");
        if (control && !control.checkValidity()) {
          control.reportValidity();
          throw new Error(`${field.display_name}: ${control.validationMessage}`);
        }
      }
      // Only changed fields are converted. Preserve untouched nulls, numbers,
      // identifiers and structured data, and do not write all the empty fields.
      const attributes = { ...baseline.attribute_values };
      for (const field of changedFields) {
        const value = parseValue(
          draft[field.draftKey], rosterFieldType(field, valueFor(baseline, field)), field.display_name,
        );
        if (field.objectKey) {
          const original = attributes[field.attribute_id];
          const members = { ...(original && typeof original === "object" ? original : {}) } as Record<string, unknown>;
          if (value == null) delete members[field.objectKey];
          else members[field.objectKey] = value;
          attributes[field.attribute_id] = members;
        } else attributes[field.attribute_id] = value;
      }
      if (nameChanged && Object.hasOwn(attributes, "employee_name")) attributes.employee_name = name;
      const result = await update.mutateAsync({
        employeeId: employee.id,
        ...(nameChanged ? { employee_name: name } : {}),
        ...(changedFields.length > 0 || (nameChanged && Object.hasOwn(attributes, "employee_name"))
          ? { attribute_values: attributes } : {}),
        ...(baseline.updated_at ? { expected_updated_at: baseline.updated_at } : {}),
      });
      setBaseline(result);
      setSaved(true);
      finishEditing();
      toast.success("Roster changes saved");
      if (blockerRef.current.status === "blocked") blockerRef.current.proceed();
    } catch (caught) {
      setError(`${formatError(caught)} Your changes are still here and have not been saved.`);
    }
  };

  const actions = (
    <div className="w-full">
          {error && <p role="alert" className="mb-3 text-sm text-error">{error}</p>}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div role="status" className="text-sm text-muted-foreground">
              {update.isPending ? "Saving changes…" : dirty
                ? `${changeCount} unsaved change${changeCount === 1 ? "" : "s"}` : "No changes yet"}
            </div>
            <div className="flex gap-2">
              <Button variant="outline" disabled={update.isPending}
                onClick={() => dirty ? setCancelPrompt(true) : finishEditing()}>Cancel</Button>
              <Button disabled={!dirty} loading={update.isPending} onClick={() => void save()}>
                <Save className="size-4" aria-hidden="true" />Save changes
              </Button>
            </div>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">Saving updates matching fields. Run matching to re-evaluate plan assignments.</p>
        </div>
  );

  return (
    <section ref={editorSection} aria-labelledby="employee-roster-heading" className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id="employee-roster-heading" className="text-sm font-semibold">Roster data</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            {editing ? "Changes are saved only when you select Save changes." : "Select Edit roster data to change employee category or other uploaded fields, then Save changes."}
          </p>
        </div>
        {!editing && canEdit && (
          <Button ref={editButton} variant="outline" size="sm" disabled={disabled} onClick={startEditing}>
            <Pencil className="size-4" aria-hidden="true" />Edit roster data
          </Button>
        )}
      </div>
      {disabled && <p className="text-xs text-muted-foreground">Save the mapping changes before editing roster data.</p>}
      {!editing && saved && (
        <p role="status" className="flex items-center gap-1.5 text-sm text-success">
          <Check className="size-4" aria-hidden="true" />All changes saved
        </p>
      )}
      <div className="grid grid-cols-1 gap-x-5 sm:grid-cols-2">
        <div className="min-w-0 border-b border-border py-3">
          <p className="mb-1.5 text-xs text-muted-foreground">Staff ID <span className="text-muted-foreground">(read only)</span></p>
          <p className="break-words text-sm">{employee.staff_id}</p>
        </div>
        <div className="min-w-0 border-b border-border py-3">
          {editing ? (
            <label className="block text-xs text-muted-foreground">
              Employee Name
              <Input autoFocus value={name} disabled={update.isPending}
                onChange={(event) => { setName(event.target.value); setError(null); }}
                className="mt-1.5 text-base sm:text-sm" />
            </label>
          ) : (
            <><p className="mb-1.5 text-xs text-muted-foreground">Employee Name</p>
              <p className="break-words text-sm">{displayValue(employee.employee_name)}</p></>
          )}
        </div>
        {fields.map((field) => {
          const key = field.draftKey;
          const original = valueFor(current, field);
          const empty = original == null || original === "";
          return (
            <div key={key} data-roster-field={key} className="min-w-0 border-b border-border py-3">
              {editing ? (
                <RosterFieldInput field={field} original={original} value={draft[key] ?? ""}
                  disabled={update.isPending}
                  onChange={(value, badInput) => {
                    setDraft((previous) => ({ ...previous, [key]: value }));
                    setBadInputs((previous) => {
                      const next = new Set(previous);
                      if (badInput) next.add(key); else next.delete(key);
                      return next;
                    });
                    setError(null);
                  }} />
              ) : (
                <><p className="mb-1.5 break-words text-xs text-muted-foreground">{field.display_name}</p>
                  <p className={`whitespace-pre-wrap break-words text-sm ${empty ? "text-muted-foreground" : "text-foreground"}`}>
                    {displayValue(original)}
                  </p></>
              )}
            </div>
          );
        })}
      </div>
      {editing && (actionContainer ? createPortal(actions, actionContainer) : actions)}
      <AlertDialog open={cancelPrompt || leaving}
        onOpenChange={(open) => {
          if (open || update.isPending) return;
          setCancelPrompt(false);
          if (blocker.status === "blocked") blocker.reset();
        }}
        title="Unsaved roster changes"
        description={<div className="space-y-2"><p>Your edits to this employee have not been saved.</p>
          {error && <p role="alert" className="text-error">{error}</p>}</div>}
        tone="info" cancelLabel="Continue editing"
        confirmLabel={leaving ? "Save and leave" : "Save changes"} confirmVariant="default"
        loading={update.isPending} onConfirm={() => void save()}
        secondaryLabel={leaving ? "Discard and leave" : "Discard changes"}
        secondaryVariant="destructiveOutline" onSecondary={discard} />
    </section>
  );
}
