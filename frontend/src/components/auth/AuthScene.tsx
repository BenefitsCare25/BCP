import type { ReactNode } from "react";
import { AuthPanel } from "./AuthPanel";

/**
 * Shared sign-in shell for both surfaces (broker Entra + member OTP): a clean
 * white form column beside a light product-showcase panel. The form content
 * (logo, heading, fields, footer) lives in one column that is centered within
 * the left panel. Type, inputs and buttons are the platform's own, so this
 * reads as the same product a signed-in user already knows.
 */
export function AuthScene({
  eyebrow,
  title,
  subtitle,
  children,
  secondary,
}: {
  eyebrow: string;
  title: ReactNode;
  subtitle: string;
  children: ReactNode;
  secondary?: ReactNode;
}) {
  return (
    <div className="flex min-h-screen w-full flex-col bg-background lg:h-screen lg:flex-row">
      {/* ── Form column ─────────────────────────────────────────────── */}
      <section className="relative flex flex-1 flex-col bg-background px-6 py-8 sm:px-10 lg:h-screen lg:w-[45%] lg:overflow-y-auto lg:px-12 lg:py-10">
        <div className="mx-auto flex w-full max-w-sm flex-1 flex-col">
          <img
            src="/inspro-logo-mark.png"
            alt="Inspro Insurance Brokers"
            className="signin-in h-12 w-auto self-start sm:h-14"
          />

          <div className="flex flex-1 flex-col justify-center py-10">
            <p
              className="signin-in text-2xs font-semibold uppercase tracking-[0.16em] text-primary"
              style={{ animationDelay: "0.04s" }}
            >
              {eyebrow}
            </p>
            <h1
              className="signin-in mt-3 text-balance text-[2rem] font-semibold leading-[1.08] tracking-[-0.02em] text-foreground sm:text-[2.375rem]"
              style={{ animationDelay: "0.08s" }}
            >
              {title}
            </h1>
            <p
              className="signin-in mt-2.5 text-md leading-relaxed text-muted-foreground"
              style={{ animationDelay: "0.12s" }}
            >
              {subtitle}
            </p>

            <div className="signin-in mt-8" style={{ animationDelay: "0.16s" }}>
              {children}

              {secondary && (
                <>
                  <div className="my-5 flex items-center gap-3 text-xs font-medium text-muted-foreground">
                    <span className="h-px flex-1 bg-border" />
                    OR
                    <span className="h-px flex-1 bg-border" />
                  </div>
                  {secondary}
                </>
              )}
            </div>
          </div>

          <footer className="flex items-center justify-between text-xs text-subtle">
            <span>© {new Date().getFullYear()} Inspro Insurance Brokers</span>
            <span className="inline-flex items-center gap-1.5">
              <span className="size-1.5 rounded-full bg-good" />
              Secure sign-in
            </span>
          </footer>
        </div>
      </section>

      {/* ── Brand panel ─────────────────────────────────────────────── */}
      <section className="min-h-[42vh] p-4 sm:p-5 lg:h-screen lg:min-h-0 lg:w-[55%] lg:py-5 lg:pl-0 lg:pr-5">
        <AuthPanel />
      </section>
    </div>
  );
}
