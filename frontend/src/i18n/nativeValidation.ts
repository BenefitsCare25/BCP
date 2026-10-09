import { translatePortalText, type PortalLocale } from "./portal";

type Control = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
const isControl = (target: EventTarget | null): target is Control => target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement;

/** Keep native validation rules and focus behavior, using the chosen portal language. */
export function installPortalValidation(locale: PortalLocale) {
  const customized = new Set<Control>();
  const pt = (source: string, values?: readonly unknown[]) => translatePortalText(source, locale, values);
  const clear = (control: Control) => {
    if (!customized.delete(control)) return;
    control.setCustomValidity("");
  };
  const onInput = (event: Event) => { if (isControl(event.target)) clear(event.target); };
  const onInvalid = (event: Event) => {
    const control = event.target;
    if (!isControl(control)) return;
    // An application's own custom business error keeps its existing handler.
    if (control.validity.customError && !customized.has(control)) return;
    clear(control);
    const validity = control.validity;
    if (validity.valid) { event.preventDefault(); return; }
    let message = pt("Enter a valid value.");
    if (validity.valueMissing) message = pt(control instanceof HTMLSelectElement ? "Select an option" : "Fill in this required field.");
    else if (validity.typeMismatch && control instanceof HTMLInputElement && control.type === "email") message = pt("Enter a valid email address.");
    else if (validity.patternMismatch) message = pt("Use the required format.");
    else if (validity.tooShort && "minLength" in control) message = pt("Use at least {0} characters.", [control.minLength]);
    else if (validity.tooLong && "maxLength" in control) message = pt("Use no more than {0} characters.", [control.maxLength]);
    else if (control instanceof HTMLInputElement && (validity.rangeUnderflow || validity.rangeOverflow)) {
      const bound = validity.rangeUnderflow ? control.min : control.max;
      if (control.type === "date") message = pt(validity.rangeUnderflow ? "Choose a date on or after {0}." : "Choose a date on or before {0}.", [new Date(`${bound}T00:00:00Z`)]);
      else message = pt(validity.rangeUnderflow ? "Enter a value of at least {0}." : "Enter a value no greater than {0}.", [bound]);
    } else if (validity.stepMismatch) message = pt("Use the allowed increment for this field.");
    control.setCustomValidity(message);
    customized.add(control);
  };
  // React/autofill may change a control without a user input event. Clearing
  // our message lets native constraints evaluate its new value normally.
  const observer = new MutationObserver(records => {
    for (const record of records) if (isControl(record.target)) clear(record.target);
  });
  observer.observe(document, { subtree: true, attributes: true, attributeFilter: ["value", "min", "max", "minlength", "maxlength", "required", "pattern", "step"] });
  document.addEventListener("invalid", onInvalid, true);
  document.addEventListener("input", onInput, true);
  document.addEventListener("change", onInput, true);
  return () => {
    document.removeEventListener("invalid", onInvalid, true);
    document.removeEventListener("input", onInput, true);
    document.removeEventListener("change", onInput, true);
    observer.disconnect();
    for (const control of customized) control.setCustomValidity("");
  };
}
