import { usePortalTranslation } from "@/i18n/portal";
import { Shield } from "lucide-react";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { useBrand } from "@/components/brand/BrandProvider";

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
  const pt = usePortalTranslation();
  const brand = useBrand();
  const label = memberId
    ? pt("Temporary card preview for {0}, {1} member ID {2}", [memberName || pt("member"), memberId.insurer ?? pt("insurer"), memberId.id])
    : pt("Temporary {0} card preview; not an issued panel card", [brand.product_name]);
  return (
    <div className="portal-hero-card-wrap">
      <div className="portal-temporary-card" role="img" aria-label={pt(label)}>
        <div className="portal-temporary-card-top">
          <BrandLogo variant="header" decorative />
          <Shield size={44} strokeWidth={1.1} aria-hidden="true" />
        </div>
        <div className="portal-temporary-card-bottom">
          {memberId && (
            <span className="portal-temporary-card-id">
              {memberId.insurer ? `${memberId.insurer} · ` : ""}{memberId.id}
            </span>
          )}
          <strong>{memberName || pt("Member")}</strong>
          {companyName && <span>{companyName}</span>}
        </div>
        <span className="portal-temporary-card-mark">{pt("Preview")}</span>
      </div>
    </div>
  );
}
