import { useEffect } from "react";
import { useRouterState } from "@tanstack/react-router";
import { Languages } from "lucide-react";
import { isPortalSurface, usePortalLocale, usePortalTranslation, type PortalLocale } from "./portal";
import "./portal-language.css";
import { installPortalValidation } from "./nativeValidation";

export function PortalLanguageControl() {
  const { locale, setLocale } = usePortalLocale();
  const t = usePortalTranslation();
  return (
    <label className="portal-language-control">
      <Languages size={18} aria-hidden="true" />
      <span className="sr-only">{t("Language")}</span>
      <select aria-label={t("Language")} value={locale} onChange={event => setLocale(event.target.value as PortalLocale)}>
        <option value="en-SG" lang="en">English</option>
        <option value="zh-SG" lang="zh-Hans">简体中文</option>
      </select>
    </label>
  );
}

/** Restores the document language when leaving either portal for the broker. */
export function PortalDocumentLanguage() {
  const path = useRouterState({ select: state => state.location.pathname });
  const { locale } = usePortalLocale();
  useEffect(() => {
    document.documentElement.lang = isPortalSurface(path) && locale === "zh-SG" ? "zh-Hans-SG" : "en";
  }, [path, locale]);
  useEffect(() => isPortalSurface(path) ? installPortalValidation(locale) : undefined, [path, locale]);
  return null;
}
