/** "Find a clinic" — panel clinics covered under the member's policy, nearest
 * first once they say where they are. Mirrored read-only in the broker
 * employee-view preview.
 *
 * The page carries no heading and no lede: the shell owns the h1, the nav says
 * which section this is, and the first control on the screen ("Where you are")
 * explains the page better than a sentence about it would. */
import { usePortalClinics } from "@/api/portal";
import { ClinicLocator } from "@/components/portal/ClinicLocator";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

export function PortalClinicsPage() {
  useDocumentTitle("Find a clinic");
  return <ClinicLocator useClinicsQuery={usePortalClinics} />;
}
