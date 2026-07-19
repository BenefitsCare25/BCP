"""Seed vocabulary for the insurer name catalog (Singapore).

Canonical short ``name`` values are the ones brokers actually type and that
already sit in ``Product.insurer`` / roster ``"<Insurer> Member ID"`` headers —
so they are deliberately short, not the registered entity name. ``legal_name``
is the MAS-licensed entity, sourced from the LIA member list (direct life
insurers) and the GIA member directory (general insurers).

``aliases`` capture the other spellings the same insurer appears under on
placement slips and rosters, including pre-rebrand names (AXA → HSBC Life,
NTUC Income → Income, Aviva → Singlife). They exist to catch duplicate catalog
entries, not to rewrite stored values.

Where an insurer runs separate life and general entities in Singapore (Great
Eastern, Tokio Marine, Zurich), the entry follows the one used for group
employee benefits and names the sibling entity in ``notes``.
"""
from __future__ import annotations

from typing import TypedDict


class InsurerSeed(TypedDict, total=False):
    name: str
    legal_name: str
    aliases: list[str]
    notes: str


SG_INSURERS: list[InsurerSeed] = [
    {
        "name": "AIA",
        "legal_name": "AIA Singapore Private Limited",
        "aliases": ["AIA Singapore", "AIA Singapore Pte Ltd", "AIA Group"],
    },
    {
        "name": "AIG",
        "legal_name": "AIG Asia Pacific Insurance Pte. Ltd.",
        "aliases": [
            "AIG Asia Pacific",
            "AIG Singapore",
            "American International Group",
        ],
    },
    {
        "name": "Allianz",
        "legal_name": "Allianz Insurance Singapore Pte. Ltd.",
        "aliases": [
            "Allianz Singapore",
            "Allianz Global Corporate & Specialty",
            "AGCS",
        ],
        "notes": (
            "Allianz Global Corporate & Specialty SE, Singapore Branch is a "
            "separate MAS-licensed entity used for corporate/specialty lines."
        ),
    },
    {
        "name": "Allied World",
        "legal_name": "Allied World Assurance Company, Ltd (Singapore Branch)",
        "aliases": ["AWAC", "Allied World Assurance"],
    },
    {
        "name": "Berkley",
        "legal_name": "Berkley Insurance Company (Singapore Branch)",
        "aliases": [
            "BERKLEY INSURANCE",
            "Berkley Insurance Asia",
            "W. R. Berkley",
            "WR Berkley",
        ],
        "notes": "Trades as Berkley Insurance Asia and Berkley Re Asia.",
    },
    {
        "name": "Berkshire",
        "legal_name": "Berkshire Hathaway Specialty Insurance Company",
        "aliases": ["BHSI", "Berkshire Hathaway Specialty", "Berkshire Hathaway"],
    },
    {
        "name": "Chubb",
        "legal_name": "Chubb Insurance Singapore Limited",
        "aliases": ["Chubb Singapore", "ACE Insurance", "ACE"],
        "notes": "Formerly ACE Insurance, renamed after the 2016 Chubb merger.",
    },
    {
        "name": "EQ Insurance",
        "legal_name": "EQ Insurance Company Limited",
        "aliases": ["EQI", "EQ"],
    },
    {
        "name": "Great Eastern",
        "legal_name": "The Great Eastern Life Assurance Company Limited",
        "aliases": [
            "GE",
            "GEL",
            "Great Eastern Life",
            "Great Eastern General",
            "Great Eastern General Insurance Limited",
            "GEGI",
        ],
        "notes": (
            "Two Singapore entities: Great Eastern Life (group life/medical) "
            "and Great Eastern General Insurance Limited (GPA, WICA, travel). "
            "Split into a second catalog entry if the reports must separate them."
        ),
    },
    {
        "name": "HL Assurance",
        "legal_name": "HL Assurance Pte. Ltd.",
        "aliases": ["HLAS", "Hong Leong Assurance", "HL"],
    },
    {
        "name": "HSBC Life",
        "legal_name": "HSBC Life (Singapore) Pte. Ltd.",
        "aliases": [
            "HSBC Life Singapore",
            "HSBC",
            "AXA",
            "AXA Insurance",
            "AXA Insurance Pte Ltd",
            "AXA Singapore",
        ],
        "notes": (
            "Formerly AXA Insurance Pte Ltd; HSBC completed the acquisition "
            "and rebrand to HSBC Life (Singapore) in 2022. Older placement "
            "slips still say AXA."
        ),
    },
    {
        "name": "Income",
        "legal_name": "Income Insurance Limited",
        "aliases": [
            "NTUC Income",
            "NTUC",
            "NTUC Income Insurance Co-operative Limited",
            "Income Insurance",
        ],
        "notes": (
            "Corporatised from NTUC Income Insurance Co-operative Limited on "
            "1 September 2022."
        ),
    },
    {
        "name": "Liberty",
        "legal_name": "Liberty Insurance Pte Ltd",
        "aliases": ["Liberty Insurance", "Liberty Singapore"],
        "notes": (
            "Effective 1 January 2026 Liberty Insurance Pte Ltd transferred "
            "its entire insurance business to Liberty Specialty Markets "
            "Singapore Pte. Limited (see the LSM entry). Kept separate here "
            "because both names appear on historical slips — merge them if "
            "your reports should treat them as one insurer."
        ),
    },
    {
        "name": "LSM",
        "legal_name": "Liberty Specialty Markets Singapore Pte. Limited",
        "aliases": ["Liberty Specialty Markets", "Liberty Mutual"],
        "notes": "Received the Liberty Insurance Pte Ltd book on 1 January 2026.",
    },
    {
        "name": "MSIG",
        "legal_name": "MSIG Insurance (Singapore) Pte. Ltd.",
        "aliases": [
            "MSIG Singapore",
            "Mitsui Sumitomo Insurance",
            "Mitsui Sumitomo",
        ],
    },
    {
        "name": "QBE",
        "legal_name": "QBE Insurance (Singapore) Pte Ltd",
        "aliases": ["QBE Singapore", "QBE Insurance"],
    },
    {
        "name": "Raffles Health",
        "legal_name": "Raffles Health Insurance Pte. Ltd.",
        "aliases": ["RHI", "Raffles Health Insurance", "Raffles"],
    },
    {
        "name": "Singlife",
        "legal_name": "Singapore Life Ltd.",
        "aliases": [
            "Singapore Life",
            "Singlife with Aviva",
            "Aviva",
            "Aviva Ltd",
            "Aviva Singapore",
        ],
        "notes": (
            "Merged with Aviva Singapore on 1 January 2022, traded as "
            "'Singlife with Aviva', and became simply Singlife on "
            "1 January 2023."
        ),
    },
    {
        "name": "Tokio Marine Life",
        "legal_name": "Tokio Marine Life Insurance Singapore Ltd.",
        "aliases": ["TMLS", "Tokio Marine", "Tokio Marine Life Singapore"],
        "notes": (
            "The life entity. Tokio Marine Insurance Singapore Ltd is the "
            "separate general insurer."
        ),
    },
    {
        "name": "Zurich",
        "legal_name": "Zurich Insurance Company Ltd (Singapore Branch)",
        "aliases": [
            "Zurich Singapore",
            "Zurich Insurance",
            "Zurich Life",
            "Zurich Life Insurance (Singapore)",
        ],
        "notes": (
            "General branch. Zurich Life Insurance (Singapore) Pte Ltd is the "
            "separate life entity."
        ),
    },
]
