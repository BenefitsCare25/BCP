import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { HomeMosaic } from "@/components/portal/HomeMosaic";

/** `/portal` — the first destination.
 *
 * The portal used to land on Coverage, which answered one of the four questions
 * members arrive with and buried the other three a tap away. The mosaic answers
 * all four at a glance and leads into Coverage's three tabs from the tiles that
 * summarise them, so nothing was demoted — the home IS the overview.
 *
 * No heading here: `PortalShell` carries the one `h1` (the member's name), and
 * the tiles are self-labelling. */
export function PortalHomePage() {
  useDocumentTitle("My benefits");
  return <HomeMosaic />;
}
