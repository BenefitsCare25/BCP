import { useSyncExternalStore } from "react";
import chinese from "./zh-SG.json";
import insuranceChinese from "./insurance.zh-SG.json";
import diagnosisChinese from "./diagnoses.zh-SG.json";
import { generatedPortalCopy } from "./generatedCopy";
import { translateInsuranceText } from "./insuranceCopy";

export type PortalLocale = "en-SG" | "zh-SG";
export const PORTAL_LOCALE_KEY = "inspro.portal.language";
const listeners = new Set<() => void>();
let preferred: PortalLocale = "en-SG";
try {
  if (localStorage.getItem(PORTAL_LOCALE_KEY) === "zh-SG") preferred = "zh-SG";
} catch { /* The current tab still remembers the choice when storage is blocked. */ }

export function isPortalSurface(path = typeof window === "undefined" ? "" : window.location.pathname): boolean {
  return /^\/(?:portal|hr)(?:\/|$)/.test(path);
}

export function getPortalLocale(): PortalLocale {
  return isPortalSurface() ? preferred : "en-SG";
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function setPortalLocale(locale: PortalLocale) {
  preferred = locale;
  try { localStorage.setItem(PORTAL_LOCALE_KEY, locale); } catch { /* Tab-only fallback. */ }
  listeners.forEach(listener => listener());
}

if (typeof window !== "undefined") window.addEventListener("storage", event => {
  if (event.key !== PORTAL_LOCALE_KEY && event.key !== null) return;
  preferred = event.newValue === "zh-SG" ? "zh-SG" : "en-SG";
  listeners.forEach(listener => listener());
});

const catalog: Readonly<Record<string, string>> = { ...chinese, ...insuranceChinese, ...diagnosisChinese };
const normalizedCatalog = new Map(Object.entries(catalog).map(([source, value]) => [source.trim().toLowerCase(), value]));
/** Keep generated copy in source form so an existing error or note can change language. */
export interface PortalMessage {
  readonly portalSource: string;
  readonly values: readonly unknown[];
}
export type PortalCopy = string | PortalMessage;
export function portalMessage(portalSource: string, values: readonly unknown[] = []): PortalMessage {
  return { portalSource, values };
}
function isPortalMessage(value: unknown): value is PortalMessage {
  return typeof value === "object" && value !== null && "portalSource" in value && "values" in value;
}
export interface PortalTranslator {
  (source: PortalCopy, values?: readonly unknown[]): string;
  (source: string, values?: readonly unknown[]): string;
  <T>(source: T, values?: readonly unknown[]): T;
}

export function translatePortalText(source: string, locale: PortalLocale, values: readonly unknown[] = []): string {
  if (locale === "zh-SG" && !catalog[source] && !normalizedCatalog.has(source.trim().toLowerCase())) {
    const generated = generatedPortalCopy(source);
    if (generated) return translatePortalText(generated.portalSource, locale, generated.values);
  }
  const text = locale === "zh-SG" ? translateInsuranceText(source, key => catalog[key] ?? normalizedCatalog.get(key.trim().toLowerCase())) ?? source : source;
  // A single pass prevents a value containing {0} from being interpreted as copy.
  return text.replace(/\{(\d+)\}/g, (match, index: string) => {
    if (Number(index) >= values.length) return match;
    const value = values[Number(index)];
    if (isPortalMessage(value)) return translatePortalText(value.portalSource, locale, value.values);
    if (value instanceof Date) return new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(value);
    return String(value ?? "");
  });
}

function translateCopy(source: unknown, locale: PortalLocale, values?: readonly unknown[]) {
  if (isPortalMessage(source)) return translatePortalText(source.portalSource, locale, source.values);
  return typeof source === "string" ? translatePortalText(source, locale, values) : source;
}

/** For pure presentation helpers. Unknown policy wording always stays verbatim. */
export const portalText = ((source: unknown, values?: readonly unknown[]) => translateCopy(source, getPortalLocale(), values)) as PortalTranslator;

export function usePortalLocale() {
  const locale = useSyncExternalStore(subscribe, getPortalLocale, () => "en-SG" as const);
  return { locale, setLocale: setPortalLocale };
}

export function usePortalTranslation(): PortalTranslator {
  const { locale } = usePortalLocale();
  return ((source: unknown, values?: readonly unknown[]) => translateCopy(source, locale, values)) as PortalTranslator;
}
