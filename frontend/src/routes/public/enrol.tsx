import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CalendarClock, LinkIcon, PauseCircle } from "lucide-react";
import { usePortalLocale, usePortalTranslation } from "@/i18n/portal";
import {
  fetchPublicForm,
  LinkError,
  retryLinkDelay,
  retryLinkQuery,
  submitPublicForm,
  useLinkToken,
  type PublicForm,
  type PublicReceipt,
} from "@/api/publicLinks";
import { Deck, type DeckSlide } from "@/components/portal/leaf/Deck";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { LinkNotice, PublicLinkFrame } from "@/components/publicLinks/PublicLinkFrame";
import { BenefitsStep } from "@/components/publicEnrol/BenefitsStep";
import { DeclarationsStep } from "@/components/publicEnrol/DeclarationsStep";
import { DetailsStep } from "@/components/publicEnrol/DetailsStep";
import { FamilyStep } from "@/components/publicEnrol/FamilyStep";
import { Receipt } from "@/components/publicEnrol/Receipt";
import { ReviewStep } from "@/components/publicEnrol/ReviewStep";
import { allProblems, emptyDraft, payload, type Draft, type StepKey } from "@/components/publicEnrol/formState";
import { parseServerDate } from "@/lib/format";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

const TOKEN_KEY = "inspro.enrolLink";

function when(value: string, locale: string): string {
  return parseServerDate(value).toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
}

export function PublicEnrolPage() {
  const pt = usePortalTranslation();
  useDocumentTitle(pt("Enrolment form"));
  const token = useLinkToken(TOKEN_KEY);
  const query = useQuery({
    queryKey: ["public-enrol", token],
    queryFn: () => fetchPublicForm(token!),
    enabled: !!token,
    retry: retryLinkQuery,
    retryDelay: retryLinkDelay,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    meta: { localErrorHandling: true },
  });

  if (!token || (query.error instanceof LinkError && query.error.status === 404)) {
    return (
      <PublicLinkFrame>
        <LinkNotice icon={<LinkIcon className="size-6" />} title="This link is not valid">
          <p>{pt("Open the enrolment link exactly as your HR team sent it, or ask them for the latest one.")}</p>
        </LinkNotice>
      </PublicLinkFrame>
    );
  }
  if (query.isPending) {
    return <PublicLinkFrame wide enrolment><LeafSkeleton /></PublicLinkFrame>;
  }
  if (query.isError) {
    return (
      <PublicLinkFrame>
        <PortalErrorState onRetry={() => void query.refetch()} />
      </PublicLinkFrame>
    );
  }
  return <FormStates key={token} token={token} form={query.data} />;
}

function FormStates({ token, form }: { token: string; form: PublicForm }) {
  const pt = usePortalTranslation();
  const { locale } = usePortalLocale();
  if (form.status === "open") return <OpenForm token={token} form={form} />;
  const notice = {
    paused: { icon: <PauseCircle className="size-6" />, title: "This form is paused", body: "Your company's broker has paused this form for now. Please try again later or ask your HR team." },
    not_yet_open: { icon: <CalendarClock className="size-6" />, title: "This form is not open yet", body: "Enrolment opens on {0}." },
    closed: { icon: <CalendarClock className="size-6" />, title: "Enrolment has closed", body: "This enrolment period closed on {0}. Ask your HR team if you missed it or have a life event to report." },
  }[form.status];
  return (
    <PublicLinkFrame company={form.company_name}>
      <LinkNotice icon={notice.icon} title={notice.title} tone="warn">
        <p className="font-semibold text-record">{form.window_name}</p>
        <p>{pt(notice.body, [when(form.status === "not_yet_open" ? form.opens_at : form.closes_at, locale)])}</p>
      </LinkNotice>
    </PublicLinkFrame>
  );
}

function OpenForm({ token, form }: { token: string; form: PublicForm }) {
  const pt = usePortalTranslation();
  const { locale } = usePortalLocale();
  const [draft, setDraft] = useState<Draft>(() => emptyDraft(form));
  const [step, setStep] = useState<StepKey>("details");
  const [touched, setTouched] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [serverProblems, setServerProblems] = useState<string[]>([]);
  const [receipt, setReceipt] = useState<PublicReceipt | null>(null);
  const problems = useMemo(() => allProblems(form, draft), [form, draft]);

  const change = (next: Draft) => { setDraft(next); setTouched(true); };
  useEffect(() => {
    if (!touched || receipt) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [touched, receipt]);

  const go = (key: StepKey) => {
    setStep(key);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const submit = async () => {
    setSubmitting(true);
    setServerProblems([]);
    try {
      setReceipt(await submitPublicForm(token, payload(form, draft)));
      window.scrollTo({ top: 0 });
    } catch (e) {
      if (e instanceof LinkError && e.problems.length) setServerProblems(e.problems);
      else setServerProblems([e instanceof Error ? e.message : "Something went wrong. Please try again."]);
    } finally {
      setSubmitting(false);
    }
  };

  if (receipt) {
    return (
      <PublicLinkFrame company={form.company_name}>
        <Receipt token={token} form={form} receipt={receipt} onAnother={() => {
          setReceipt(null); setDraft(emptyDraft(form)); setTouched(false); setStep("details");
        }} />
      </PublicLinkFrame>
    );
  }

  const mark = (key: StepKey) => {
    const n = problems.filter(p => p.step === key).length;
    if (!touched) return undefined;
    return n ? pt("{0} to check", [n]) : pt("Done");
  };
  const slides: DeckSlide[] = [
    { key: "details", label: pt("Your details"), mark: mark("details"),
      render: () => <DetailsStep form={form} draft={draft} onChange={change} /> },
    { key: "family", label: pt("Family"), mark: draft.dependants.length ? pt("{0} named", [draft.dependants.length]) : undefined,
      render: () => <FamilyStep form={form} draft={draft} onChange={change} /> },
    { key: "benefits", label: pt("Benefit choices"), mark: mark("benefits"),
      render: () => <BenefitsStep form={form} draft={draft} onChange={change} /> },
    { key: "declarations", label: pt("Declarations"), mark: mark("declarations"),
      render: () => <DeclarationsStep token={token} form={form} draft={draft} onChange={change} /> },
    { key: "review", label: pt("Review and sign"),
      render: () => (
        <ReviewStep form={form} draft={draft} problems={problems} serverProblems={serverProblems}
          submitting={submitting} onChange={change} onGo={go} onSubmit={() => void submit()} />
      ) },
  ];

  return (
    <PublicLinkFrame company={form.company_name} wide enrolment>
      <header className="mb-4 space-y-1.5">
        <h1 className="text-2xl font-bold tracking-title text-record sm:text-3xl">{pt(form.title ?? "Enrolment form")}</h1>
        <p className="flex flex-wrap gap-x-3 gap-y-1 text-row text-label">
          <span className="font-semibold text-record">{form.window_name}</span>
          <span>{pt("Closes {0}", [when(form.closes_at, locale)])}</span>
        </p>
        {form.intro_lines.length > 0 && (
          <div className="max-w-3xl space-y-1 pt-1 text-row text-record">
            {form.intro_lines.map(line => <p key={line}>{pt(line)}</p>)}
          </div>
        )}
      </header>
      <Deck slides={slides} label={pt("Enrolment form steps")} itemNoun="step"
        activeKey={step} onActiveKeyChange={key => go(key as StepKey)} />
    </PublicLinkFrame>
  );
}
