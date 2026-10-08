import { useId, type ReactNode } from "react";
import { LockKeyhole } from "lucide-react";
import { BrokerLoginScenery } from "./BrokerLoginScenery";
import "./broker-login.css";
import { BrandLogo, PoweredBy } from "@/components/brand/BrandLogo";

export function BrokerLoginScene({
  children,
  title = "Welcome back",
  subtitle = "Sign in to your broker portal.",
}: {
  children: ReactNode;
  title?: string;
  subtitle?: string;
}) {
  const headingId = useId();
  return (
    <main className="broker-login" aria-labelledby={headingId}>
      <BrokerLoginScenery />
      <section className="broker-login__content">
        <svg className="broker-login__curve" viewBox="0 0 100 1000" preserveAspectRatio="none" aria-hidden="true">
          <path d="M 25 0 C 105 175 -30 350 12 540 C 40 715 110 830 20 1000 L 100 1000 L 100 0 Z" />
        </svg>
        <div className="broker-login__form">
          <BrandLogo
            variant="lockup"
            className="broker-login__logo"
            width={200}
            height={64}
            wordmarkClassName="broker-login__wordmark"
          />
          <h1 id={headingId}>{title}</h1>
          <p className="broker-login__subtitle">{subtitle}</p>
          <div className="broker-login__actions">{children}</div>
          <p className="broker-login__support">Need access? <span>Contact your administrator.</span></p>
        </div>
        <footer className="broker-login__footer">
          <span className="broker-login__secure">
            <LockKeyhole size={14} aria-hidden="true" />
            <span>Secure sign-in</span>
          </span>
          <PoweredBy className="broker-login__attribution" />
        </footer>
      </section>
    </main>
  );
}
