import { useId, type ReactNode } from "react";
import { LockKeyhole } from "lucide-react";
import { currentHrTenantSlug, currentPortalTenantSlug, tenantSurfaceUrl } from "@/lib/tenant";
import { LoginScenery } from "./LoginScenery";
import "./portal-login.css";
import { BrandLogo, PoweredBy } from "@/components/brand/BrandLogo";

export function PortalLoginScene({
  role, company, title, subtitle, children,
}: {
  role: "employee" | "hr";
  company: string;
  title: string;
  subtitle: string;
  children: ReactNode;
}) {
  const headingId = useId();
  const draft = company.trim().toLowerCase();
  const validDraft = draft.length <= 63 && /^(?!-)(?!.*--)[a-z0-9-]+(?<!-)$/.test(draft);
  const slug = (role === "employee" ? currentPortalTenantSlug() : currentHrTenantSlug()) || (validDraft ? draft : "");

  return (
    <main className="portal-login" aria-labelledby={headingId}>
      <div className="portal-login__stage">
        <LoginScenery />
        <section className="portal-login__content">
          <div className="portal-login__form">
            <header className="portal-login__brand">
              <BrandLogo
                variant="lockup"
                className="portal-login__logo"
                width={200}
                height={64}
                wordmarkClassName="portal-login__wordmark"
              />
            </header>
            <h1 id={headingId}>{title}</h1>
            <p className="portal-login__subtitle">{subtitle}</p>
            <nav className="portal-login__roles" aria-label="Sign-in role">
              <a href={tenantSurfaceUrl("portal", slug, "/portal/sign-in")} aria-current={role === "employee" ? "page" : undefined}>Employee</a>
              <a href={tenantSurfaceUrl("hr", slug, "/hr/sign-in")} aria-current={role === "hr" ? "page" : undefined}>HR admin</a>
            </nav>
            {children}
            <p className="portal-login__support">Need access? <span>Contact your {role === "employee" ? "HR team" : "company administrator"}.</span></p>
          </div>
          <footer className="portal-login__footer">
            <span className="portal-login__secure"><LockKeyhole size={14} aria-hidden="true" /> Secure sign-in</span>
            <PoweredBy className="portal-login__attribution" />
          </footer>
        </section>
      </div>
    </main>
  );
}
