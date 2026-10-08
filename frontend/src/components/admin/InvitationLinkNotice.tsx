import { useId, useRef } from "react";
import { Copy, X } from "lucide-react";
import { toast } from "sonner";
import type { CreatedInvitation } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmtDateTime } from "@/lib/format";

/** The single-use acceptance link for a new invitation, shown once, right
 *  after it is created. The server builds the link on the firm's own staff
 *  address; without one there is no link, and none is made up here from the
 *  address this page happens to be on. */
export function InvitationLinkNotice({
  invitation,
  onDismiss,
}: {
  invitation: CreatedInvitation;
  onDismiss: () => void;
}) {
  const id = useId();
  const linkRef = useRef<HTMLInputElement>(null);
  const link = invitation.invite_url;

  const copy = async () => {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      toast.success("Invitation link copied");
    } catch {
      linkRef.current?.select();
      toast.error("Couldn't copy. The link is selected: copy it with your keyboard.");
    }
  };

  return (
    <section
      aria-labelledby={`${id}-title`}
      className="space-y-2 rounded-md border border-border bg-card p-3 text-sm"
    >
      <div className="flex items-start justify-between gap-2">
        <h4 id={`${id}-title`} className="font-medium text-foreground">
          {link ? `Invitation link for ${invitation.email}` : `No invitation link for ${invitation.email}`}
        </h4>
        <Button
          type="button"
          size="icon-sm"
          variant="ghost"
          aria-label="Dismiss invitation link"
          onClick={onDismiss}
        >
          <X className="size-4" aria-hidden="true" />
        </Button>
      </div>
      {link ? (
        <>
          <p id={`${id}-help`} className="text-xs text-muted-foreground">
            Send this link to them yourself. It works once, for this invitation only,
            and is shown only now: copy it before closing this message.
            {invitation.expires_at ? ` Valid until ${fmtDateTime(invitation.expires_at)}.` : ""}
          </p>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <Input
              ref={linkRef}
              readOnly
              value={link}
              aria-label={`Invitation link for ${invitation.email}`}
              aria-describedby={`${id}-help`}
              onFocus={(event) => event.currentTarget.select()}
              className="min-w-0 flex-1 font-mono text-xs"
            />
            <Button type="button" size="sm" variant="outline" onClick={() => void copy()}>
              <Copy className="size-3.5" aria-hidden="true" /> Copy link
            </Button>
          </div>
        </>
      ) : (
        <p className="text-xs text-muted-foreground">
          The invitation was created, but this firm has no web address for its staff
          site yet, so there is nowhere for an invitation link to point and it cannot
          be shared another way. Once an address is active under Web addresses, revoke
          this invitation and invite them again to get a link.
        </p>
      )}
    </section>
  );
}
