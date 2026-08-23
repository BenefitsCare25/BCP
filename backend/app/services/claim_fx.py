"""Applying a currency conversion to a claim, and reading one back.

`services/fx.py` knows about currencies and dates. This knows about claims: when
a conversion is recomputed, what it means when there isn't one, and what a claim
is worth in the currency every limit is denominated in.

**A claim has exactly three currency states**, derived — never stored as a
fourth column that could disagree with the figures:

- ``not_required`` — the claim is already in the policy currency. `amount_converted`
  is NULL, and that is not a gap: `amount_claimed` IS the SGD figure.
- ``converted``   — a foreign claim carrying an SGD equivalent, either from the
  reference rate or keyed in by a broker (`fx_source` says which).
- ``unavailable`` — a foreign claim with no SGD equivalent, because the rate
  could not be fetched. The claim is live and the member is not blocked; the
  review flags it and the approve guard refuses to settle it until a person
  supplies the figure.

**A conversion is a function of exactly three claim facts** — amount, currency,
incurred date. When any of them moves the previous answer is about a claim that
no longer exists, so the amendment path re-prices from scratch: keeping it would
leave an SGD figure attached to a different foreign amount, which is precisely
the silent wrongness this module exists to prevent.

**But nothing else re-prices a figure an assessor keyed in.** `apply_conversion`
also runs where no fact has moved — a `needs_info` resubmission, a member
confirming the number — and there a broker's hand-keyed conversion must survive
untouched (`replace_manual`). It is a deliberate act with an audit row behind it;
discarding it on a resubmission would strand the claim back at `unavailable`, or
quietly replace a considered figure with a market one, with nothing recording
either. Both broker endpoints 409 to prevent exactly that, so letting submit do
it would only be the same bug by a longer route.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Claim
from app.schemas.claims import FxQuoteOut
from app.services.fx import POLICY_CURRENCY, FxQuote, quote

logger = logging.getLogger(__name__)

FX_STATE_NOT_REQUIRED = "not_required"
FX_STATE_CONVERTED = "converted"
FX_STATE_UNAVAILABLE = "unavailable"

# Who produced `amount_converted`. "frankfurter" = the reference rate;
# "broker" = an assessor keyed the SGD figure in because no rate could be had.
FX_SOURCE_BROKER = "broker"

# Amending any of these invalidates the conversion. Imported by the amendment
# path so the two can never drift apart.
FX_INPUT_FIELDS = frozenset({"amount_claimed", "currency", "incurred_date"})


def is_foreign(claim: Claim) -> bool:
    return (claim.currency or POLICY_CURRENCY).strip().upper() != POLICY_CURRENCY


def fx_state(claim: Claim) -> str:
    """Which of the three currency states this claim is in. See the module docstring."""
    if not is_foreign(claim):
        return FX_STATE_NOT_REQUIRED
    return FX_STATE_CONVERTED if claim.amount_converted is not None else FX_STATE_UNAVAILABLE


def policy_amount(claim: Claim) -> float | None:
    """What the claim is worth in the policy currency, or None if nobody knows.

    **None is a real answer and callers must handle it**, which is the entire
    point of returning it. The tempting shape — ``amount_converted or
    amount_claimed`` — reads a foreign figure as an SGD one whenever the
    conversion is missing, so a USD 500 bill lands in an SGD limit as 500 and
    every downstream sum is quietly wrong by the exchange rate. That was the
    behaviour this module replaced.
    """
    if not is_foreign(claim):
        return float(claim.amount_claimed or 0.0)
    if claim.amount_converted is None:
        return None
    return float(claim.amount_converted)


def clear_conversion(claim: Claim) -> None:
    """Drop the conversion and everything asserted about it."""
    claim.amount_converted = None
    claim.fx_rate = None
    claim.fx_rate_date = None
    claim.fx_source = None
    claim.fx_acknowledged_at = None


def apply_conversion(
    db: Session, claim: Claim, *, replace_manual: bool = False
) -> FxQuote | None:
    """Recompute `amount_converted` + the `fx_*` trail from the claim's own facts.

    Idempotent and total: whatever the claim carried before, afterwards it holds
    the conversion its current amount/currency/date imply, or none at all.

    **Clears the member's acknowledgement whenever the figure moves.** The
    acknowledgement is consent to a specific number; carrying it across a
    changed number would record the member as having accepted a figure they were
    never shown. Submit re-asks in that case.

    ``replace_manual`` decides the fate of a figure an ASSESSOR keyed in, and
    the default protects it. A broker-supplied conversion is a deliberate act
    recorded in the audit trail, and this function runs on paths that are not
    re-pricing anything: a `needs_info` resubmission, a member confirming the
    figure. Left unguarded it silently discarded the assessor's number —
    reverting the claim to `unavailable` when the rate was still missing, or
    overwriting a considered figure with a market one when it came back, in both
    cases with nothing in the trail to say it happened. That is precisely what
    `set_claim_conversion` and `refresh_claim_conversion` 409 to prevent, so
    reaching the same outcome through submit would just be the same bug by a
    longer route.

    Pass True only where the claim's own facts moved — the amendment path — and
    the previous answer is therefore about a claim that no longer exists.
    """
    previous = claim.amount_converted
    previous_ack = claim.fx_acknowledged_at

    if not is_foreign(claim):
        clear_conversion(claim)
        return None

    if claim.fx_source == FX_SOURCE_BROKER and not replace_manual:
        return None

    fx = quote(db, claim.currency, claim.incurred_date)
    if fx is None:
        clear_conversion(claim)
        return None

    converted = fx.convert(float(claim.amount_claimed or 0.0))
    converted_amount = Decimal(str(converted)).quantize(Decimal("0.01"))
    claim.amount_converted = converted_amount
    claim.fx_rate = Decimal(str(fx.rate)).quantize(Decimal("0.00000001"))
    claim.fx_rate_date = fx.rate_date
    claim.fx_source = fx.source
    # Unchanged figure → the member's existing acknowledgement still describes
    # what they saw, so a re-save (or a needs_info resubmission) must not force
    # them to confirm the same number twice.
    claim.fx_acknowledged_at = (
        previous_ack
        if previous is not None and previous == converted_amount
        else None
    )
    return fx


def build_quote(db: Session, *, currency: str, amount: float, on: date) -> FxQuoteOut:
    """A conversion preview for a form, before anything is saved.

    ONE builder for both surfaces — the member's claim form and the broker's
    LOG-case form — because the number a claimant is asked to accept and the
    number an assessor records have to be the same number. Two endpoints each
    doing their own multiplication is how they come to differ by a cent.

    Never raises. An unavailable rate is a described outcome (`available=False`
    plus a `note` saying so), not an error the form has to interpret.
    """
    code = (currency or "").strip().upper()
    # `amount` is carried at FULL precision and only the conversion is rounded —
    # `FxQuote.convert` does that, and `apply_conversion` calls it with the same
    # raw `amount_claimed`. Rounding the input here first made the two paths
    # multiply different numbers, so a member entering more than two decimals
    # could be quoted a figure the server would then refuse to accept as their
    # acknowledgement (the tolerance is half a cent).
    base = FxQuoteOut(
        currency=code, policy_currency=POLICY_CURRENCY, amount=float(amount)
    )
    if not code or code == POLICY_CURRENCY:
        return base.model_copy(
            update={
                "available": True,
                "converted": base.amount,
                "rate": 1.0,
                "as_of_date": on,
                "note": None,
            }
        )

    fx = quote(db, code, on)
    if fx is None:
        return base.model_copy(
            update={
                "as_of_date": on,
                "note": (
                    f"We could not get an exchange rate for {code} on "
                    f"{on.isoformat()}. Your claim can still be sent — it will "
                    "be converted by hand when it is reviewed."
                ),
            }
        )

    converted = fx.convert(base.amount)
    if fx.stale:
        # Named plainly. "Rate as of an earlier date" reads as an error to a
        # claimant unless it says why, and the why is mundane: rates are not
        # published every day.
        note = (
            f"{code} {base.amount:,.2f} is {POLICY_CURRENCY} {converted:,.2f}, "
            f"using the rate published on {fx.rate_date.isoformat()} — no rate "
            f"is published for {fx.as_of_date.isoformat()}."
        )
    else:
        note = (
            f"{code} {base.amount:,.2f} is {POLICY_CURRENCY} {converted:,.2f} at "
            f"the {fx.rate_date.isoformat()} rate."
        )
    return base.model_copy(
        update={
            "available": True,
            "converted": converted,
            "rate": fx.rate,
            "as_of_date": fx.as_of_date,
            "rate_date": fx.rate_date,
            "stale": fx.stale,
            "source": fx.source,
            "note": note,
        }
    )


def set_manual_conversion(claim: Claim, converted: float) -> None:
    """Record an SGD figure an assessor supplied because no rate could be fetched.

    Stamped with an implied rate so the register and the reports can still show
    one — it is a real rate (this many SGD per unit billed), it just came from a
    person. `fx_rate_date` stays NULL: nothing was published, so naming a date
    would dress a judgement call as a market fact.
    """
    claim.amount_converted = Decimal(str(round(float(converted), 2))).quantize(
        Decimal("0.01")
    )
    amount = float(claim.amount_claimed or 0.0)
    claim.fx_rate = (
        (claim.amount_converted / Decimal(str(amount))).quantize(Decimal("0.000001"))
        if amount
        else None
    )
    claim.fx_rate_date = None
    claim.fx_source = FX_SOURCE_BROKER
    # **The claimant has not accepted THIS figure**, so any earlier consent is
    # dropped and, if the claim ever passes back through submit (the
    # `needs_info` round trip), they are asked about the new one.
    #
    # Asking is right rather than pedantic: a member is reimbursed against this
    # number whoever produced it, and a figure a PERSON chose is the one most
    # worth putting in front of them. It costs nothing in the ordinary flow —
    # an assessor prices the claim at the moment they approve it, and an
    # approved claim never returns to submit.
    claim.fx_acknowledged_at = None
