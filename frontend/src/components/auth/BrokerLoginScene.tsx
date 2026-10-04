import { useId, type ReactNode } from "react";
import { LockKeyhole } from "lucide-react";
import { BrokerLoginScenery } from "./BrokerLoginScenery";
import "./broker-login.css";

export function BrokerLoginScene({ children }: { children: ReactNode }) {
  const headingId = useId();
  return (
    <main className="broker-login" aria-labelledby={headingId}>
      <BrokerLoginScenery />
      <section className="broker-login__content">
        <svg className="broker-login__curve" viewBox="0 0 100 1000" preserveAspectRatio="none" aria-hidden="true">
          <path d="M 25 0 C 105 175 -30 350 12 540 C 40 715 110 830 20 1000 L 100 1000 L 100 0 Z" />
        </svg>
        <div className="broker-login__form">
          <img
            className="broker-login__logo"
            src="/inspro-logo-mark.png"
            alt="Inspro Insurance Brokers"
            width="200"
            height="64"
          />
          <h1 id={headingId}>Welcome back</h1>
          <p className="broker-login__subtitle">Sign in to your broker portal.</p>
          <div className="broker-login__actions">{children}</div>
          <p className="broker-login__support">Need access? <span>Contact your administrator.</span></p>
        </div>
        <footer className="broker-login__footer">
          <LockKeyhole size={14} aria-hidden="true" />
          <span>Secure sign-in</span>
        </footer>
      </section>
    </main>
  );
}
