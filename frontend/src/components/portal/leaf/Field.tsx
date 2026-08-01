/** Form fields on the leaf.
 *
 * This exists to make the portal's two most common accessibility defects
 * unrepresentable rather than merely fixed:
 *
 * 1. **A label bound to nothing.** Seven `<Label>`s on the claim form sat as
 *    siblings of their control with no `htmlFor`, so the accessible name was
 *    empty (WCAG 1.3.1, 4.1.2). `Field` owns the id and hands it to the child,
 *    so there is no way to render a label without binding it.
 * 2. **An error nobody is told about.** No input carried `aria-invalid` or
 *    `aria-describedby` and no error container carried `role="alert"`, so a
 *    failed submit was silent to a screen reader and rendered below the fold
 *    for everyone else (WCAG 3.3.1, 4.1.3). Passing `error` wires all three.
 *
 * Controls render at 16px on touch viewports: anything smaller makes iOS Safari
 * zoom the page on focus and breaks the column (The Reach Rule). */
import { useId, type ReactNode } from "react";
import { cn } from "@/lib/cn";

/** A control sits ON the glass, so it cannot be glass itself — it takes a
 * near-solid fill so its edge and its value read against the pane behind it.
 * `border-leaf-input` is the 1.4.11 edge (3.70:1 on the hardest glass);
 * `border-hairline` would be a decorative rule and fails there. */
export const leafControl =
  "w-full min-h-11 rounded-control border border-leaf-input bg-bar/80 px-3 py-2 " +
  "text-base sm:text-row text-record placeholder:text-label leaf-focus " +
  "aria-[invalid=true]:border-strike-rejected";

export function Field({
  label,
  hint,
  error,
  required,
  children,
  className,
}: {
  label: ReactNode;
  /** Plain-language help. Never behind hover — a member on a phone can't hover. */
  hint?: ReactNode;
  error?: string | null;
  required?: boolean;
  children: (props: {
    id: string;
    "aria-invalid": boolean;
    "aria-describedby": string | undefined;
    "aria-required": boolean | undefined;
  }) => ReactNode;
  className?: string;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy =
    [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") ||
    undefined;

  return (
    <div className={cn("space-y-1.5", className)}>
      <label htmlFor={id} className="leaf-label block">
        {label}
        {required && <RequiredMark />}
      </label>
      {hint && (
        <p id={hintId} className="text-row text-label">
          {hint}
        </p>
      )}
      {children({
        id,
        "aria-invalid": Boolean(error),
        "aria-describedby": describedBy,
        "aria-required": required || undefined,
      })}
      {error && (
        <p
          id={errorId}
          // Announced the moment it appears: a failed submit renders below the
          // fold as often as not, and these used to be silent `<p>`s.
          role="alert"
          className="text-row font-medium text-strike-rejected"
        >
          {error}
        </p>
      )}
    </div>
  );
}

/** A labelled GROUP of controls (an upload set, a picker, a radio-like row).
 *
 * These used `<Label>`, which renders a real `<label>` — and a `<label>` whose
 * `htmlFor` points at nothing labels nothing, so the group read as unnamed
 * while looking correct on screen (WCAG 1.3.1 / 4.1.2). A group needs a group
 * role and an accessible name, not a control label. */
export function FieldGroup({
  label,
  hint,
  error,
  children,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  children: ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={cn("space-y-1.5", className)} role="group" aria-labelledby={id}>
      <span id={id} className="leaf-label block">
        {label}
      </span>
      {hint && <p className="text-row text-label">{hint}</p>}
      {children}
      {error && (
        <p role="alert" className="text-row font-medium text-strike-rejected">
          {error}
        </p>
      )}
    </div>
  );
}

/** The uppercase "(required)" marker, so every required control on the member
 * surface is marked the same way — a red asterisk is a convention the portal's
 * audience does not share, and colour alone is never a marker (WCAG 1.4.1). */
export function RequiredMark() {
  return (
    <span className="ml-1 normal-case tracking-normal text-label">
      (required)
    </span>
  );
}

/** A form-level failure. `role="alert"` so it is announced the moment it
 * appears, wherever the member's focus happens to be. */
export function FormAlert({ children }: { children: ReactNode }) {
  return (
    <p
      role="alert"
      className="rounded-control border border-strike-rejected bg-bar/70 px-3 py-2 text-row text-strike-rejected"
    >
      {children}
    </p>
  );
}
