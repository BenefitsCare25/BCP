import { useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import type { PublicSite } from "@/api/public";
import type { StaffSignIn } from "@/auth/staffSignIn";
import type { BrokerSession } from "@/stores/brokerSession";
import { BrokerInviteSetPassword } from "./BrokerInviteSetPassword";
import { BrokerPasswordSignIn, type PasswordStep } from "./BrokerPasswordSignIn";
import { MicrosoftSignInButton } from "./MicrosoftSignInButton";

/** The sign-in methods this host's firm offers, in order: Microsoft 365, then
 *  email and password, with a divider when both are offered. With an
 *  invitation pending, the same methods accept it instead. */
export function BrokerSignInOptions({
  site,
  methods,
  firmName,
  inviteToken,
  denied,
  onSignedIn,
  onInviteInvalid,
}: {
  site: UseQueryResult<PublicSite>;
  methods: StaffSignIn;
  firmName: string | null;
  inviteToken: string | null;
  denied: boolean;
  onSignedIn: (session: BrokerSession) => void;
  onInviteInvalid: (message: string) => void;
}) {
  const [passwordStep, setPasswordStep] = useState<PasswordStep>("credentials");
  const entra = methods.entra !== null;
  const local = methods.local;
  const both = entra && local;

  if (site.isPending) {
    return <p role="status" className="broker-login__status">Loading sign-in options…</p>;
  }

  // A build with Microsoft values still offers Microsoft below this.
  const loadFailure = site.isError && !site.data && (
    <>
      <p role="alert" className="broker-login__alert">
        We couldn&apos;t load the sign-in options for this site. Check your connection and try again.
      </p>
      <button
        type="button"
        className="broker-login__text-button broker-login__retry"
        disabled={site.isFetching}
        onClick={() => void site.refetch()}
      >
        {site.isFetching ? "Trying again…" : "Try again"}
      </button>
    </>
  );

  if (!entra && !local) {
    if (loadFailure) return loadFailure;
    return (
      <p role="status" className="broker-login__explanation">
        {site.data?.firm
          ? `Sign-in isn't set up for ${firmName ?? "this firm"} yet. Ask your administrator to turn on Microsoft 365 or email and password sign-in.`
          : "This address isn't linked to a broker firm. Check the link you were given, or contact your administrator."}
      </p>
    );
  }

  if (inviteToken) {
    return (
      <>
        {loadFailure}
        {entra && (
          <>
            {!local && (
              <p className="broker-login__explanation">
                Continue with the Microsoft work account this invitation is for.
              </p>
            )}
            <MicrosoftSignInButton label="Accept with Microsoft" selectAccount secondary={both} />
          </>
        )}
        {both && <p className="broker-login__divider">or set a password</p>}
        {local && (
          <BrokerInviteSetPassword
            inviteToken={inviteToken}
            onAccepted={onSignedIn}
            onInvalid={onInviteInvalid}
          />
        )}
      </>
    );
  }

  const showMicrosoft = entra && passwordStep === "credentials";
  return (
    <>
      {loadFailure}
      {showMicrosoft && (
        <>
          {!local && <p className="broker-login__explanation">Continue with your Microsoft work account.</p>}
          {/* After a refusal, force the account picker: the browser still holds
              a Microsoft session, so the default flow would sign the SAME
              refused account back in and they could never switch. */}
          <MicrosoftSignInButton selectAccount={denied} secondary={both} />
        </>
      )}
      {showMicrosoft && local && <p className="broker-login__divider">or</p>}
      {local && <BrokerPasswordSignIn onSignedIn={onSignedIn} onStepChange={setPasswordStep} />}
    </>
  );
}
