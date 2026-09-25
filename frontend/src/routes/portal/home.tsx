import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { MemberHome } from "@/components/portal/home/MemberHome";

/** The member Home is separate from the broker's employee-preview mosaic. */
export function PortalHomePage() {
  useDocumentTitle("My benefits");
  return <MemberHome />;
}
