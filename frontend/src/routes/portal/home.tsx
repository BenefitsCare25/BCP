import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { MemberHome } from "@/components/portal/home/MemberHome";

/** The member Home. The broker's employee view renders the same screen
 *  (`MemberHomeView`) from its read-only preview data. */
export function PortalHomePage() {
  useDocumentTitle("My benefits");
  return <MemberHome />;
}
