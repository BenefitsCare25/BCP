import { useState } from "react";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import { ShieldAlert } from "lucide-react";
import { BrokerLoginScene } from "@/components/auth/BrokerLoginScene";
import { BrokerSignInOptions } from "@/components/auth/broker/BrokerSignInOptions";
import { signInNoticeMessage } from "@/components/auth/broker/signInMessages";
import { usePendingBrokerInvite } from "@/auth/brokerInvite";
import { acceptBrokerSession, needsTwoFactor } from "@/auth/brokerSession";
import { useSignInNotice } from "@/auth/signInNotice";
import { useStaffSignIn } from "@/auth/staffSignIn";
import type { BrokerSession } from "@/stores/brokerSession";

/**
 * Broker staff sign-in. The firm this host serves chooses the methods
 * (`/public/site`): Microsoft 365, email and password with mandatory two-factor
 * verification, or both. The root guard sends signed-out users here; the
 * route's `beforeLoad` bounces signed-in users back to / so this page never
 * flashes.
 *
 * `/sign-in#invite=<token>` turns the page into the invitation: the boot
 * sequence has already moved the token into tab storage and out of the URL.
 *
 * `?denied=1` means Microsoft (or a password) authenticated the person but the
 * platform's user list grants them nothing. Saying so is the whole point — a
 * silent return to the login screen right after a successful sign-in reads as
 * a bug, and they just retry.
 */
export function SignInPage() {
  const navigate = useNavigate();
  const denied = useRouterState({
    select: (s) => Boolean((s.location.search as { denied?: unknown }).denied),
  });
  const { site, methods } = useStaffSignIn();
  const [inviteToken, dismissInvite] = usePendingBrokerInvite();
  const notice = useSignInNotice();
  const [inviteRefusal, setInviteRefusal] = useState<string | null>(null);
  const firmName = site.data?.firm?.name ?? null;

  const finish = (session: BrokerSession) => {
    acceptBrokerSession(session);
    if (inviteToken) dismissInvite();
    void navigate({ to: needsTwoFactor(session) ? "/broker/security" : "/", replace: true });
  };

  const title = inviteToken
    ? firmName ? `You've been invited to ${firmName}` : "You've been invited"
    : "Welcome back";
  const subtitle = inviteToken
    ? "Choose how you'll sign in to accept your invitation."
    : firmName ? `Sign in to ${firmName}.` : "Sign in to your broker portal.";

  return (
    <BrokerLoginScene title={title} subtitle={subtitle}>
      {denied && (
        <div role="alert" className="broker-login__alert">
          <ShieldAlert size={18} aria-hidden="true" />
          <p>
            This account does not have access. Contact your administrator
            {methods.entra ? " or sign in with another Microsoft account." : "."}
          </p>
        </div>
      )}
      {notice && <p role="alert" className="broker-login__alert">{signInNoticeMessage(notice)}</p>}
      {inviteRefusal && <p role="alert" className="broker-login__alert">{inviteRefusal}</p>}
      <BrokerSignInOptions
        site={site}
        methods={methods}
        firmName={firmName}
        inviteToken={inviteToken}
        denied={denied}
        onSignedIn={finish}
        onInviteInvalid={(message) => {
          dismissInvite();
          setInviteRefusal(message);
        }}
      />
    </BrokerLoginScene>
  );
}
