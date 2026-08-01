/** The member's panel cards, as leaves.
 *
 * This is the one screen in the portal that gets used standing at a counter
 * with someone waiting, so it is built around what happens there: the card is
 * shown, and then a number is read out. The artwork prints that number at
 * whatever size the insurer's design chose — often 3% of the card's height,
 * which is ~10px on a phone — so the number is ALSO set as a figure beneath the
 * card, large enough to read aloud and selectable enough to copy. Duplication
 * on purpose: the printed card is the credential, the row beneath it is the
 * legible copy.
 *
 * Shared by `/portal/card` and the broker's employee-view preview, which both
 * render inside `.leaf`. The artwork rides an Authorization header, so each
 * surface injects its own blob hook (`useArtwork`) rather than a plain `src`.
 */
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import type { CardFace, MemberCard } from "@/api/panelCards";
import { CardCanvas, type ArtworkHook } from "@/components/portal/MemberCard";
import { Mount, MountRow, MountRule } from "./Mount";
import { goLinkClass, GoArrow } from "./Action";
import { glossBeside } from "./glossary";

/** The insurer's own setting names, said the way a member would ask for them.
 * "Restructured SP" is the phrase on a placement slip; nobody standing at a
 * counter thinks in it. Mirrors CARD_REMARK_LABELS in models/panel_card.py —
 * an unknown key falls through to the raw one rather than being dropped, so a
 * remark the broker wrote is never silently withheld. */
const REMARK_GLOSS: Record<string, string> = {
  gp: "At a GP clinic",
  ae: "At A&E",
  restructured_sp: "At a public hospital specialist",
  private_sp: "At a private specialist",
  general: "Anywhere",
};

/** The values worth setting as readable text under the artwork. Deliberately
 * short: everything else on the card is already printed on it, and a list of
 * every placement key would bury the one number that gets asked for. */
const COUNTER_FIELDS: { key: string; label: string }[] = [
  { key: "member_id", label: "Member ID" },
  { key: "policy_number", label: "Policy number" },
];

/** A placement key as a member would read it. There is a proper label map on
 * the broker's card-options endpoint, but the portal doesn't fetch it — and
 * "member_name" → "Member name" is honest without one. */
function keyLabel(key: string): string {
  const words = key.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function holderLabel(card: MemberCard): string {
  if (card.holder_type === "employee") return "You";
  return card.holder_name ?? "Family member";
}

function MemberCardLeaf({
  card,
  useArtwork,
}: {
  card: MemberCard;
  useArtwork: ArtworkHook;
}) {
  const [face, setFace] = useState<CardFace>("front");
  const showingBack = face === "back" && card.has_back;
  // Both faces are fetched so flipping doesn't flash; the back only when it exists.
  const front = useArtwork(card.card_id, "front");
  const back = useArtwork(card.card_id, "back", card.has_back);

  const fields = card.placements.fields.filter(
    (f) => f.face === (showingBack ? "back" : "front"),
  );
  const remarks = Object.entries(card.remarks).filter(([, v]) => v);

  // `CardCanvas` only prints the placed fields when it HAS the artwork they were
  // placed against — so when the artwork is missing everything printed on the
  // card is gone from the screen. The two counter fields alone would drop the
  // member's name, plan and expiry with it, which is most of what a clinic asks
  // for. So: artwork present → the two fields a counter asks for by name;
  // artwork genuinely unavailable → every value the card would have printed,
  // since this list is now the card.
  //
  // **`loading` is not "unavailable".** The list is expanded only once the fetch
  // has actually resolved; expanding it while the blob is in flight made every
  // card visibly reflow on load, from two rows to a dozen and back.
  //
  // The expansion is also scoped to the FACE ON SCREEN. Built from
  // `card.placements.fields` unfiltered, a failed BACK blob printed the front's
  // keys underneath it — values that are not on the face the member is looking
  // at.
  const artwork = showingBack ? back : front;
  const placedKeys = [
    ...new Set(fields.map((f) => f.key)),
  ].filter((key) => card.values[key]);
  const counterOnly = COUNTER_FIELDS.filter(({ key }) => card.values[key]);
  const counter =
    artwork.status === "ready" || artwork.status === "loading"
      ? counterOnly
      : [
          ...counterOnly,
          ...placedKeys
            .filter((key) => !COUNTER_FIELDS.some((f) => f.key === key))
            .map((key) => ({ key, label: keyLabel(key) })),
        ];

  return (
    <Mount
      as="article"
      label={card.product_name}
      gloss={glossBeside(card.product_name, card.product_code, card.product_name)}
      aside={
        <span className="leaf-label block max-w-28 truncate sm:max-w-40">
          {holderLabel(card)}
        </span>
      }
    >
      <CardCanvas
        aspectRatio={card.aspect_ratio}
        artworkSrc={artwork.url}
        artworkStatus={artwork.status}
        fields={fields}
        values={card.values}
        // The artwork is the INSURER's object, not ours: it takes the control
        // radius and a hairline, with no shadow of its own, so it sits inside
        // the glass as a mounted specimen rather than reading as a second tile
        // floating on the first (no nested cards).
        className="rounded-control border-hairline/75 shadow-none"
        fallback="Your insurer hasn't supplied the card design yet — the details below are still what a clinic needs."
        errorFallback="The card design couldn't be loaded just now — the details below are still what a clinic needs."
      />

      {card.has_back && (
        <button
          type="button"
          onClick={() => setFace(showingBack ? "front" : "back")}
          className={goLinkClass({ className: "mt-2" })}
        >
          {showingBack ? "Show the front" : "Show the back"}
        </button>
      )}

      {counter.length > 0 && (
        <dl className="mt-3">
          {counter.map(({ key, label }) => (
            <MountRow key={key} term={label}>
              {/* select-all so one tap selects the whole number — at a counter
                  this gets copied into a form or read out, never edited. */}
              <span className="select-all font-semibold">
                {card.values[key]}
              </span>
            </MountRow>
          ))}
        </dl>
      )}

      {card.services.length > 0 && (
        <div className="mt-3">
          <p className="leaf-label">Covered here</p>
          <p className="mt-1 text-row text-record">
            {card.services.map((s) => s.label).join(" · ")}
          </p>
        </div>
      )}

      {(remarks.length > 0 || card.special_conditions) && (
        <>
          <MountRule className="my-3" />
          <dl>
            {remarks.map(([key, value]) => (
              <MountRow key={key} term={REMARK_GLOSS[key] ?? key}>
                {value}
              </MountRow>
            ))}
            {card.special_conditions && (
              <MountRow term="Also note">{card.special_conditions}</MountRow>
            )}
          </dl>
        </>
      )}
    </Mount>
  );
}

/** No card issued yet.
 *
 * The audit measured this state as a 496px dead end — a dashed box saying no
 * cards exist, on the screen a member opened *because* they were about to walk
 * into a clinic. It now says who issues one and what to do in the meantime,
 * and offers the next useful move rather than ending the page.
 *
 * `action` is opt-out because the broker preview renders this too, where the
 * member's clinic route is not navigable. */
function NoCards({ message, action }: { message: string; action: boolean }) {
  return (
    <Mount label="No card issued yet">
      <p className="text-row text-label">{message}</p>
      {action && (
        <Link
          to="/portal/clinics"
          className={goLinkClass({ className: "mt-2" })}
        >
          Find a panel clinic
          <GoArrow />
        </Link>
      )}
    </Mount>
  );
}

export function CardLeaf({
  cards,
  useArtwork,
  emptyMessage = "Your HR team adds your panel card once your plan is set up with the insurer. Until it appears, ask them for your member number before you visit a panel clinic.",
  emptyAction = true,
}: {
  cards: MemberCard[];
  useArtwork: ArtworkHook;
  emptyMessage?: string;
  emptyAction?: boolean;
}) {
  if (cards.length === 0) {
    return <NoCards message={emptyMessage} action={emptyAction} />;
  }
  return (
    // One column on a phone and two only from `sm`, where a card at half width
    // is still wider than the phone it was legible on (The Whole-Frame Rule:
    // a mount reflows by whole frames, never fractions).
    <div className="grid gap-3 sm:grid-cols-2">
      {cards.map((card) => (
        <MemberCardLeaf
          key={`${card.assignment_id}-${card.holder_id}`}
          card={card}
          useArtwork={useArtwork}
        />
      ))}
    </div>
  );
}
