import { Shield } from "lucide-react";

export function TemporaryCard({ memberName, companyName }: { memberName: string; companyName: string }) {
  return (
    <div className="portal-hero-card-wrap">
      <div className="portal-temporary-card" role="img" aria-label="Temporary Inspro card preview; not an issued panel card">
        <div className="portal-temporary-card-top">
          <img src="/inspro-logo-header.png" alt="" aria-hidden="true" />
          <Shield size={55} strokeWidth={1.1} aria-hidden="true" />
        </div>
        <div className="portal-temporary-card-bottom">
          <strong>{memberName || "Member"}</strong>
          {companyName && <span>{companyName}</span>}
        </div>
        <span className="portal-temporary-card-mark">Preview</span>
      </div>
    </div>
  );
}
