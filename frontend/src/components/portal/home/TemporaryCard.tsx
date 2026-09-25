import { Shield } from "lucide-react";

/** Stand-in until the insurer supplies card artwork. It is marked "Preview"
 *  and never imitates an insurer's design — but it DOES carry the member's
 *  insurer ID when one exists, because that number is what a clinic counter
 *  asks for, and the member used to be told to go ask HR for it while the
 *  claim form was already displaying it. */
export function TemporaryCard({
  memberName,
  companyName,
  memberId,
}: {
  memberName: string;
  companyName: string;
  memberId?: { insurer: string | null; id: string } | null;
}) {
  const label = memberId
    ? `Temporary card preview for ${memberName || "member"}, ${memberId.insurer ?? "insurer"} member ID ${memberId.id}`
    : "Temporary Inspro card preview; not an issued panel card";
  return (
    <div className="portal-hero-card-wrap">
      <div className="portal-temporary-card" role="img" aria-label={label}>
        <div className="portal-temporary-card-top">
          <img src="/inspro-logo-header.png" alt="" aria-hidden="true" />
          <Shield size={44} strokeWidth={1.1} aria-hidden="true" />
        </div>
        <div className="portal-temporary-card-bottom">
          {memberId && (
            <span className="portal-temporary-card-id">
              {memberId.insurer ? `${memberId.insurer} · ` : ""}{memberId.id}
            </span>
          )}
          <strong>{memberName || "Member"}</strong>
          {companyName && <span>{companyName}</span>}
        </div>
        <span className="portal-temporary-card-mark">Preview</span>
      </div>
    </div>
  );
}
