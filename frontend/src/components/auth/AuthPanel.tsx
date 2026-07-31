import { BadgeCheck, HeartPulse, ShieldCheck } from "lucide-react";

/**
 * Right-hand brand panel of the sign-in surface — a clean, near-white showcase
 * (no photography, no brand-red fill). Its focal subject is a floating digital
 * benefits card (a real Inspro member feature), framed by product accents on a
 * light surface. Colors are theme tokens; red appears only as a small accent.
 * Motion is a single slow float, disabled under reduced motion.
 */

const COVERAGE = ["Hospital & Surgical", "GP", "Dental", "Specialist"];

const AVATARS = [
  { initials: "AT", tint: "bg-brand-500" },
  { initials: "KM", tint: "bg-brand-600" },
  { initials: "RL", tint: "bg-brand-800" },
];

export function AuthPanel() {
  return (
    <div className="signin-panel signin-shadow-frame relative h-full w-full overflow-hidden rounded-3xl text-foreground ring-1 ring-border">
      {/* No logo here — the single brand lockup lives at the page's top-left,
          on the form column. This panel is purely the product showcase. */}

      {/* ── Focal object: floating digital benefits card ─────────────────── */}
      <div
        className="signin-in absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2"
        style={{ animationDelay: "0.1s" }}
      >
        <div className="-rotate-[5deg]">
          <div className="signin-float signin-shadow-card w-[22rem] max-w-[80vw] rounded-3xl border border-border bg-card p-6">
            <div className="flex items-center justify-between">
              <span className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                <ShieldCheck className="size-4 text-primary" strokeWidth={2.25} />
                Inspro · Panel card
              </span>
              <span className="text-xs font-medium text-muted-foreground">
                2027
              </span>
            </div>

            <p className="mt-8 text-lg font-semibold tracking-tight text-foreground">
              Alexandra Tan
            </p>
            <p className="mt-0.5 font-mono text-sm tracking-[0.22em] text-muted-foreground">
              •••• •••• 4921
            </p>

            <div className="mt-5 flex flex-wrap gap-1.5">
              {COVERAGE.map((c) => (
                <span
                  key={c}
                  className="rounded-md bg-accent px-2 py-0.5 text-2xs font-medium text-accent-foreground"
                >
                  {c}
                </span>
              ))}
            </div>

            <div className="mt-6 flex items-center justify-between border-t border-border pt-4">
              <span className="inline-flex items-center gap-1.5 text-2xs font-medium text-muted-foreground">
                <span className="size-1.5 rounded-full bg-good" />
                Coverage active
              </span>
              <HeartPulse className="size-4 text-primary" strokeWidth={2.25} />
            </div>
          </div>
        </div>

        {/* Claim-approved toast, overlapping the card's top-right */}
        <div
          className="signin-in signin-shadow-card absolute -right-8 -top-11 w-64 rounded-2xl border border-border bg-card p-3.5"
          style={{ animationDelay: "0.28s" }}
        >
          <div className="flex items-center gap-3">
            <span className="grid size-9 shrink-0 place-items-center rounded-full bg-good-soft text-good">
              <BadgeCheck className="size-5" strokeWidth={2.25} />
            </span>
            <div className="min-w-0">
              <p className="whitespace-nowrap text-sm font-semibold leading-tight text-foreground">
                Claim approved
              </p>
              <p className="mt-0.5 truncate text-xs text-muted-foreground">
                Outpatient · GP visit
              </p>
            </div>
            <span className="ml-auto shrink-0 text-sm font-semibold tabular-nums text-foreground">
              S$420
            </span>
          </div>
        </div>

        {/* Member cluster, overlapping the card's bottom-left */}
        <div
          className="signin-in signin-shadow-card absolute -bottom-6 -left-7 flex items-center gap-3 rounded-full border border-border bg-card py-1.5 pl-1.5 pr-4"
          style={{ animationDelay: "0.36s" }}
        >
          <div className="flex -space-x-2.5">
            {AVATARS.map((a) => (
              <span
                key={a.initials}
                className={`grid size-8 place-items-center rounded-full ${a.tint} text-2xs font-semibold text-primary-foreground ring-2 ring-card`}
              >
                {a.initials}
              </span>
            ))}
          </div>
          <span className="text-xs font-medium leading-tight text-foreground">
            <span className="font-semibold">128 members</span>
            <br />
            enrolled today
          </span>
        </div>
      </div>

      {/* Quiet brand line, bottom */}
      <p className="absolute inset-x-8 bottom-8 text-sm font-medium text-muted-foreground">
        Group benefits, made operational.
      </p>
    </div>
  );
}
