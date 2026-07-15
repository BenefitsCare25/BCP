/** Member clinic locator — find the nearest panel clinic covered by the
 * member's policy. Mirrored read-only in the broker employee-view preview. */
import { usePortalClinics } from "@/api/portal";
import { ClinicLocator } from "@/components/portal/ClinicLocator";

export function PortalClinicsPage() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-foreground">Find a clinic</h1>
        <p className="text-sm text-muted-foreground">
          Panel clinics covered under your policy — use your device location or
          enter a postal code to see the 10 nearest first.
        </p>
      </div>
      <ClinicLocator useClinicsQuery={usePortalClinics} />
    </div>
  );
}
