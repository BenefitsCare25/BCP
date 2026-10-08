/** How a broker's own web address goes live. The platform console activates
 *  it; the firm console only requests it, so the last step differs. */
export function DomainSetupGuide({ audience }: { audience: "platform" | "firm" }) {
  return (
    <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
      <li>
        {audience === "platform"
          ? "The broker adds a TXT validation record for the address at their DNS provider."
          : "Add the TXT validation record for the address at your DNS provider."}
      </li>
      <li>
        {audience === "platform"
          ? "They add a CNAME record pointing the address to the platform's edge."
          : "Add a CNAME record pointing the address to the platform's edge. Your platform contact gives you both record values."}
      </li>
      <li>Certificates are issued automatically once the records validate.</li>
      <li>
        {audience === "platform"
          ? "Activate the address here once it has validated. Until then it stays pending and serves nothing."
          : "The platform activates the address once it validates. Until then it stays pending and serves nothing."}
      </li>
    </ol>
  );
}
