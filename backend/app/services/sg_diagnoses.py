"""Bundled diagnosis catalog for the claim form's searchable picker.

Singapore publishes no public diagnosis-catalog API — data.gov.sg / MOH only
release aggregate ICD-10-AM statistics (e.g. "Top 10 Conditions of
Hospitalisation") — so this is a curated list in the style group insurers /
TPAs use, labelled with lay synonyms ("Varicella / Chickenpox") and tagged
with the WHO ICD-10 category code where one applies.

Groups mirror the claim-intake profiles (`claim_intake.py`):
``gp`` outpatient primary care, ``sp`` specialist outpatient, ``hospital``
inpatient / day surgery, ``dental``, ``maternity``. An entry may sit in
several groups. The member can always fall back to "Other" (free text) in
the UI — the catalog narrows search, it doesn't exhaustively gate.
"""
from __future__ import annotations

from typing import NamedTuple


class Diagnosis(NamedTuple):
    label: str
    icd10: str | None
    groups: frozenset[str]


DIAGNOSIS_GROUPS: tuple[str, ...] = ("gp", "sp", "hospital", "dental", "maternity")

_GP = frozenset({"gp"})
_SP = frozenset({"sp"})
_H = frozenset({"hospital"})
_D = frozenset({"dental"})
_M = frozenset({"maternity"})
_GS = _GP | _SP
_GSH = _GP | _SP | _H
_SH = _SP | _H


def _d(label: str, icd10: str | None, groups: frozenset[str]) -> Diagnosis:
    return Diagnosis(label, icd10, groups)


CATALOG: tuple[Diagnosis, ...] = (
    # ── Infections / general medicine ─────────────────────────────────────────
    _d("Upper respiratory tract infection (URTI) / Common cold", "J06", _GP),
    _d("Influenza / Flu", "J11", _GP),
    _d("COVID-19", "U07.1", _GSH),
    _d("Acute pharyngitis / Sore throat", "J02", _GP),
    _d("Acute tonsillitis", "J03", _GS),
    _d("Acute sinusitis", "J01", _GS),
    _d("Acute bronchitis", "J20", _GS),
    _d("Pneumonia / Chest infection", "J18", _GSH),
    _d("Dengue fever", "A90", _GSH),
    _d("Hand, foot and mouth disease (HFMD)", "B08.4", _GP),
    _d("Varicella / Chickenpox", "B01", _GP),
    _d("Herpes zoster / Shingles", "B02", _GS),
    _d("Viral fever / Pyrexia of unknown origin", "R50", _GSH),
    _d("Gastroenteritis / Food poisoning / Stomach flu", "A09", _GSH),
    _d("Urinary tract infection (UTI)", "N39.0", _GSH),
    _d("Conjunctivitis / Red eye", "H10", _GS),
    _d("Otitis media / Middle ear infection", "H66", _GS),
    _d("Otitis externa / Outer ear infection", "H60", _GS),
    _d("Cellulitis / Skin infection", "L03", _GSH),
    _d("Fungal skin infection / Ringworm / Tinea", "B35", _GS),
    _d("Viral warts", "B07", _GS),
    _d("Molluscum contagiosum", "B08.1", _GS),
    _d("Impetigo", "L01", _GP),
    _d("Scabies", "B86", _GP),
    _d("Typhoid / Paratyphoid fever", "A01", _H),
    _d("Tuberculosis (TB)", "A15", _SH),
    _d("Hepatitis (viral)", "B19", _SH),
    _d("Mycoplasma / Atypical pneumonia", "J15.7", _GSH),
    _d("Chikungunya", "A92.0", _GS),
    # ── Respiratory / ENT ─────────────────────────────────────────────────────
    _d("Asthma", "J45", _GSH),
    _d("Chronic obstructive pulmonary disease (COPD)", "J44", _SH),
    _d("Allergic rhinitis / Sinus allergy", "J30", _GS),
    _d("Chronic sinusitis", "J32", _GS),
    _d("Nasal polyps", "J33", _SH),
    _d("Deviated nasal septum", "J34.2", _SH),
    _d("Epistaxis / Nosebleed", "R04.0", _GS),
    _d("Tinnitus / Ringing in the ear", "H93.1", _GS),
    _d("Hearing loss", "H91", _SP),
    _d("Impacted ear wax", "H61.2", _GP),
    _d("Vertigo / Dizziness / Giddiness", "R42", _GS),
    _d("Obstructive sleep apnoea", "G47.3", _SH),
    _d("Vocal cord nodule / Hoarseness", "J38.2", _SP),
    _d("Chronic cough", "R05", _GS),
    # ── Cardiovascular / metabolic ────────────────────────────────────────────
    _d("Hypertension / High blood pressure", "I10", _GS),
    _d("Hyperlipidaemia / High cholesterol", "E78", _GS),
    _d("Diabetes mellitus", "E11", _GSH),
    _d("Pre-diabetes / Impaired glucose tolerance", "R73.0", _GP),
    _d("Ischaemic heart disease / Coronary artery disease", "I25", _SH),
    _d("Myocardial infarction / Heart attack", "I21", _H),
    _d("Atrial fibrillation / Irregular heartbeat", "I48", _SH),
    _d("Heart failure", "I50", _SH),
    _d("Palpitations", "R00.2", _GS),
    _d("Chest pain (under investigation)", "R07", _GSH),
    _d("Stroke / Cerebrovascular accident", "I63", _H),
    _d("Transient ischaemic attack (TIA)", "G45", _SH),
    _d("Varicose veins", "I83", _SH),
    _d("Deep vein thrombosis (DVT)", "I80.2", _SH),
    _d("Haemorrhoids / Piles", "K64", _GSH),
    _d("Anaemia", "D64", _GS),
    _d("Iron deficiency anaemia", "D50", _GS),
    _d("Thalassaemia", "D56", _SP),
    _d("Gout", "M10", _GS),
    _d("Thyroid disorder / Hyperthyroidism", "E05", _GS),
    _d("Hypothyroidism", "E03", _GS),
    _d("Thyroid nodule / Goitre", "E04", _SH),
    _d("Obesity / Weight management", "E66", _GS),
    _d("Underweight", "R62.8", _GP),
    _d("Vitamin D deficiency", "E55", _GP),
    # ── Gastrointestinal ──────────────────────────────────────────────────────
    _d("Gastritis / Gastric pain", "K29", _GS),
    _d("Gastro-oesophageal reflux disease (GERD) / Acid reflux", "K21", _GS),
    _d("Peptic / Gastric ulcer", "K25", _SH),
    _d("Dyspepsia / Indigestion", "K30", _GP),
    _d("Irritable bowel syndrome (IBS)", "K58", _GS),
    _d("Constipation", "K59.0", _GP),
    _d("Diarrhoea", "K59.1", _GP),
    _d("Appendicitis", "K35", _H),
    _d("Gallstones / Cholelithiasis", "K80", _SH),
    _d("Cholecystitis / Gallbladder inflammation", "K81", _H),
    _d("Hernia (inguinal / umbilical / incisional)", "K40", _SH),
    _d("Anal fissure / Fistula", "K60", _SH),
    _d("Colonic polyp", "K63.5", _SH),
    _d("Colonoscopy screening / Gastroscopy (diagnostic)", "Z12", _SH),
    _d("Inflammatory bowel disease (Crohn's / Ulcerative colitis)", "K50", _SH),
    _d("Fatty liver / Non-alcoholic fatty liver disease", "K76.0", _GS),
    _d("Abnormal liver function test", "R94.5", _GS),
    _d("Pancreatitis", "K85", _H),
    # ── Musculoskeletal / orthopaedic ─────────────────────────────────────────
    _d("Low back pain / Lumbago", "M54.5", _GS),
    _d("Neck pain / Cervicalgia", "M54.2", _GS),
    _d("Slipped disc / Prolapsed intervertebral disc", "M51.2", _SH),
    _d("Sciatica", "M54.3", _GS),
    _d("Scoliosis", "M41", _SP),
    _d("Osteoarthritis / Degenerative joint disease", "M19", _GS),
    _d("Rheumatoid arthritis", "M06", _SP),
    _d("Knee pain / Meniscus or ligament injury", "M23", _SH),
    _d("Anterior cruciate ligament (ACL) tear", "S83.5", _SH),
    _d("Shoulder pain / Rotator cuff syndrome", "M75", _GS),
    _d("Frozen shoulder / Adhesive capsulitis", "M75.0", _GS),
    _d("Tennis elbow / Lateral epicondylitis", "M77.1", _GS),
    _d("Carpal tunnel syndrome", "G56.0", _SH),
    _d("Trigger finger", "M65.3", _SH),
    _d("De Quervain's tenosynovitis", "M65.4", _GS),
    _d("Ganglion cyst", "M67.4", _SH),
    _d("Plantar fasciitis / Heel pain", "M72.2", _GS),
    _d("Ankle sprain / Ligament sprain", "S93.4", _GP),
    _d("Muscle strain / Soft tissue injury", "M62.6", _GP),
    _d("Fracture (arm / wrist / hand)", "S52", _H),
    _d("Fracture (leg / ankle / foot)", "S82", _H),
    _d("Fracture (other)", "T14.2", _H),
    _d("Bunion / Hallux valgus", "M20.1", _SH),
    _d("Osteoporosis", "M81", _SP),
    _d("Bone spur / Enthesopathy", "M77.9", _GS),
    # ── Skin ──────────────────────────────────────────────────────────────────
    _d("Eczema / Atopic dermatitis", "L20", _GS),
    _d("Contact dermatitis / Skin allergy", "L23", _GS),
    _d("Urticaria / Hives / Itchy rash", "L50", _GS),
    _d("Acne", "L70", _GS),
    _d("Psoriasis", "L40", _SP),
    _d("Vitiligo", "L80", _SP),
    _d("Alopecia / Hair loss", "L65", _GS),
    _d("Sebaceous / Epidermal cyst", "L72", _GSH),
    _d("Lipoma", "D17", _SH),
    _d("Skin tag", "L91.8", _GS),
    _d("Corn / Callus", "L84", _GP),
    _d("Ingrown toenail", "L60.0", _GSH),
    _d("Xanthelasma", "H02.6", _SP),
    _d("Keloid / Hypertrophic scar", "L91.0", _SP),
    _d("Abscess / Boil / Carbuncle", "L02", _GSH),
    _d("Melasma / Pigmentation", "L81.1", _GS),
    _d("Mole / Naevus (removal or evaluation)", "D22", _GS),
    _d("Skin lesion (under investigation)", "L98.9", _GSH),
    # ── Eye ───────────────────────────────────────────────────────────────────
    _d("Cataract", "H25", _SH),
    _d("Glaucoma", "H40", _SP),
    _d("Dry eye syndrome", "H04.1", _GS),
    _d("Stye / Chalazion / Eyelid lump", "H00", _GS),
    _d("Pterygium", "H11.0", _SH),
    _d("Floaters / Vitreous degeneration", "H43.9", _SP),
    _d("Retinal tear / Detachment", "H33", _SH),
    _d("Diabetic retinopathy", "E11.3", _SH),
    _d("Myopia / Refractive error", "H52.1", _GS),
    _d("Corneal abrasion / Foreign body in eye", "S05.0", _GSH),
    # ── Neurology / mental health ─────────────────────────────────────────────
    _d("Migraine", "G43", _GS),
    _d("Tension headache", "G44.2", _GP),
    _d("Epilepsy / Seizure disorder", "G40", _SH),
    _d("Bell's palsy / Facial nerve palsy", "G51.0", _GS),
    _d("Peripheral neuropathy / Nerve pain", "G62.9", _SP),
    _d("Insomnia / Sleep disorder", "G47.0", _GS),
    _d("Anxiety disorder", "F41", _GS),
    _d("Depression", "F32", _GS),
    _d("Stress-related condition / Adjustment disorder", "F43", _GS),
    _d("Attention deficit hyperactivity disorder (ADHD)", "F90", _SP),
    _d("Dementia / Cognitive impairment", "F03", _SP),
    # ── Urology / gynaecology / breast ────────────────────────────────────────
    _d("Kidney stone / Ureteric stone", "N20", _SH),
    _d("Chronic kidney disease", "N18", _SH),
    _d("Kidney dialysis (chronic renal failure)", "N18.5", _SH),
    _d("Prostate enlargement / BPH", "N40", _SH),
    _d("Erectile dysfunction", "N52", _SP),
    _d("Blood in urine / Haematuria", "R31", _GSH),
    _d("Ovarian cyst", "N83.2", _SH),
    _d("Uterine leiomyoma / Fibroids", "D25", _SH),
    _d("Endometriosis", "N80", _SH),
    _d("Abnormal menstruation / Menorrhagia", "N92", _GS),
    _d("Polycystic ovary syndrome (PCOS)", "E28.2", _SP),
    _d("Cervical smear abnormality", "R87.6", _SP),
    _d("Breast lump (under investigation)", "N63", _SH),
    _d("Fibroadenoma of breast", "D24", _SH),
    _d("Menopausal disorder", "N95", _GS),
    # ── Cancer / growths ──────────────────────────────────────────────────────
    _d("Cancer — breast", "C50", _SH),
    _d("Cancer — colorectal", "C18", _SH),
    _d("Cancer — lung", "C34", _SH),
    _d("Cancer — prostate", "C61", _SH),
    _d("Cancer — cervical / uterine", "C53", _SH),
    _d("Cancer — liver", "C22", _SH),
    _d("Cancer — stomach", "C16", _SH),
    _d("Cancer — nasopharyngeal (NPC)", "C11", _SH),
    _d("Cancer — lymphoma", "C85", _SH),
    _d("Cancer — leukaemia", "C95", _SH),
    _d("Cancer — thyroid", "C73", _SH),
    _d("Cancer — skin", "C44", _SH),
    _d("Cancer — other / chemotherapy or radiotherapy session", "C80", _SH),
    _d("Benign tumour / Growth (under investigation)", "D36", _SH),
    # ── Maternity ─────────────────────────────────────────────────────────────
    _d("Antenatal care / Pregnancy check-up", "Z34", _M),
    _d("Normal delivery", "O80", _M | _H),
    _d("Caesarean delivery", "O82", _M | _H),
    _d("Miscarriage / Threatened abortion", "O03", _M | _H),
    _d("Ectopic pregnancy", "O00", _M | _H),
    _d("Hyperemesis gravidarum / Severe morning sickness", "O21", _M | _H),
    _d("Gestational diabetes", "O24.4", _M),
    _d("Pre-eclampsia / Pregnancy hypertension", "O14", _M | _H),
    _d("Postnatal care", "Z39", _M),
    # ── Dental ────────────────────────────────────────────────────────────────
    _d("Dental caries / Tooth decay", "K02", _D),
    _d("Pulpitis / Toothache", "K04.0", _D),
    _d("Root canal treatment", "K04.1", _D),
    _d("Gingivitis / Gum inflammation", "K05.0", _D),
    _d("Periodontitis / Gum disease", "K05.3", _D),
    _d("Impacted wisdom tooth", "K01.1", _D | _H),
    _d("Tooth extraction", "K08.9", _D),
    _d("Dental abscess", "K04.7", _D),
    _d("Scaling and polishing / Dental cleaning", "Z13.8", _D),
    _d("Dental filling / Restoration", "K08.9", _D),
    _d("Crown / Bridge / Denture work", "K08.9", _D),
    _d("Cracked or fractured tooth", "K03.8", _D),
    _d("Bruxism / Teeth grinding", "F45.8", _D),
    _d("Temporomandibular joint (TMJ) disorder", "K07.6", _D | _SP),
    # ── Paediatric / congenital ───────────────────────────────────────────────
    # Dependant children are claimable, so chapters P (perinatal) and Q
    # (congenital) belong here even though no employee ever files against them.
    _d("Bronchiolitis (infant)", "J21", _GSH),
    _d("Croup / Acute laryngotracheitis", "J05.0", _GSH),
    _d("Febrile seizure / Febrile convulsion", "R56.0", _GSH),
    _d("Measles", "B05", _GS),
    _d("Mumps", "B26", _GS),
    # Rubella gets a row per lay name rather than the usual "A / B" label.
    # `claim_intake_suggest._resolve_diagnosis` matches a label whose tokens are
    # CONTAINED in the reading, so a 3-token "Rubella / German measles" can never
    # match the 2-token reading "German measles" — while 1-token "Measles" can,
    # and would resolve a rubella document to the wrong disease.
    _d("Rubella", "B06", _GS),
    _d("German measles", "B06", _GS),
    _d("Kawasaki disease", "M30.3", _H),
    _d("G6PD deficiency", "D55.0", _GSH),
    _d("Speech and language delay", "F80", _SP),
    _d("Developmental delay", "F88", _SP),
    _d("Autism spectrum disorder", "F84.0", _SP),
    _d("Neonatal jaundice", "P59", _H),
    _d("Preterm birth / Low birth weight (newborn care)", "P07", _H),
    _d("Neonatal respiratory distress", "P22", _H),
    _d("Neonatal infection / Sepsis", "P36", _H),
    _d("Tongue-tie / Ankyloglossia", "Q38.1", _GSH),
    _d("Congenital heart disease", "Q24.9", _SH),
    _d("Ventricular septal defect (VSD)", "Q21.0", _SH),
    _d("Atrial septal defect (ASD)", "Q21.1", _SH),
    _d("Patent ductus arteriosus (PDA)", "Q25.0", _SH),
    _d("Cleft lip / Cleft palate", "Q37", _SH),
    _d("Congenital hydrocephalus", "Q03", _SH),
    _d("Spina bifida", "Q05", _SH),
    _d("Congenital pyloric stenosis", "Q40.0", _H),
    _d("Hirschsprung's disease", "Q43.1", _SH),
    _d("Undescended testis / Cryptorchidism", "Q53", _SH),
    _d("Hypospadias", "Q54", _SH),
    _d("Developmental dysplasia of the hip", "Q65.9", _SH),
    _d("Talipes / Clubfoot", "Q66.0", _SH),
    _d("Polydactyly / Extra digits", "Q69", _SH),
    _d("Syndactyly / Fused digits", "Q70", _SH),
    _d("Down syndrome", "Q90", _SH),
    # ── Injuries / others ─────────────────────────────────────────────────────
    _d("Road traffic accident injury", "V89", _GSH),
    _d("Fall injury", "W19", _GSH),
    _d("Laceration / Open wound", "T14.1", _GSH),
    _d("Burn / Scald", "T30", _GSH),
    _d("Animal / Insect bite", "T14.1", _GP),
    _d("Head injury / Concussion", "S09.9", _H),
    _d("Foreign body ingestion / Insertion", "T18", _H),
    _d("Allergic reaction / Anaphylaxis", "T78", _GSH),
    _d("Health screening / Medical examination", "Z00.0", _GS),
    _d("Vaccination / Immunisation", "Z23", _GP),
)


def search_diagnoses(
    group: str | None, query: str, *, limit: int = 50
) -> list[Diagnosis]:
    """Case-insensitive all-tokens match on label + ICD-10 code, scoped to a
    catalog group (None = whole catalog). Prefix matches rank first so typing
    "ur" surfaces "Urticaria" before "Pleurisy"."""
    tokens = [t for t in (query or "").lower().split() if t]
    pool = [
        d for d in CATALOG
        if group is None or group in d.groups
    ]
    if not tokens:
        return sorted(pool, key=lambda d: d.label.lower())[:limit]

    def matches(d: Diagnosis) -> bool:
        hay = d.label.lower() + " " + (d.icd10 or "").lower()
        return all(t in hay for t in tokens)

    hits = [d for d in pool if matches(d)]
    first = tokens[0]

    def rank(d: Diagnosis) -> tuple[int, str]:
        label = d.label.lower()
        starts = 0 if label.startswith(first) else (
            1 if any(w.startswith(first) for w in label.split()) else 2
        )
        return (starts, label)

    return sorted(hits, key=rank)[:limit]
