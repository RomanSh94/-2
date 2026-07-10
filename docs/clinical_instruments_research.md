# Clinical Instrument Research Matrix

Governance/discovery document. Read alongside `clinical_instruments_manifest.json`
(machine-readable) and `clinical_instrument_catalog.py` (validator).

**This document contains no question wording, answer options, scoring keys,
interpretation tables, manuals, norms, percentiles, cutoffs, or translations
for any instrument.** It records bibliographic/rights identification facts
only — the kind of information found in a citation, a test-review article, or
a publisher's catalog entry, never the test content itself.

The eight URLs supplied by the owner (`psytests.org`) were used only to
identify *which* instrument is being referred to at each address, cross-checked
against academic/primary sources where possible. **They are not treated as
license or permission evidence for anything** — a third-party test-hosting
site publishing a translation is not proof of rights to reproduce, adapt, or
commercially use that content. See `test_third_party_page_is_not_license_evidence`
in the test suite for the explicit rule this encodes.

No instrument in this PR is marked `ready`. Activating any of them for real
in-product use requires a separate, later PR with documented rights evidence
that does not yet exist in this repository.

---

## 1. BDI / BDI-II — Beck Depression Inventory

| Field | Value |
|---|---|
| Instrument ID | `bdi_ii` |
| Exact full name | Beck Depression Inventory (original 1961); Beck Depression Inventory-II (1996 revision) |
| Abbreviation | BDI / BDI-II |
| Exact version | **unknown** — the source URL does not by itself establish whether it reproduces the original 1961 BDI or the 1996 BDI-II; the two have different item sets and are not interchangeable |
| Author/developer | Aaron T. Beck et al. |
| Year | 1961 (BDI); 1996 (BDI-II) |
| Construct/domain | Depression severity |
| Self-report / clinician-rated | Self-report |
| Target population | Adults (13+ in some adaptations) |
| Age range | metadata incomplete |
| Reference period | BDI-II: past 2 weeks |
| Item count | 21 (both versions) |
| Subscales | None (unidimensional total score in standard scoring) |
| Score direction | Higher = more severe depressive symptoms |
| Known risk-sensitive item | **Yes** — BDI/BDI-II item 9 addresses suicidal ideation. This is exactly the kind of risk-sensitive item that, if ever activated, must route through the existing crisis pipeline before any score is computed — not through ordinary questionnaire scoring. |
| Primary/official source | Pearson Assessments (current rights holder/publisher) |
| Russian adaptation | Multiple exist in the literature; **not verified in this PR** — which specific adaptation would be used, and under what license, is unresolved |
| Copyright/license owner | Pearson Assessments (successor to The Psychological Corporation) |
| Digital reproduction status | **permission required** — BDI/BDI-II is commercially licensed; Pearson requires purchase/license for reproduction, including digital |
| Commercial-use status | **permission required** |
| Translation-use status | **permission required** (a Russian translation/adaptation would itself need separate rights clearance even if the base instrument were licensed) |
| Can be activated now? | **No** |
| Evidence links | Owner-supplied: `https://psytests.org/depr/bdi-run.html` (identification only, not license evidence) |
| Blocking uncertainty | Exact version (BDI vs BDI-II); no license evidence in repo; Russian adaptation rights unconfirmed |

**Classification: license-gated, blocked.** No content activation without documented rights evidence.

---

## 2. HDRS / HAM-D — Hamilton Depression Rating Scale

| Field | Value |
|---|---|
| Instrument ID | `hdrs` |
| Exact full name | Hamilton Depression Rating Scale (also HAM-D, HDRS) |
| Abbreviation | HDRS / HAM-D |
| Exact version | metadata incomplete — 17-item and 21-item versions both exist; the specific version at the source page was not verified |
| Author/developer | Max Hamilton |
| Year | 1960 (original publication) |
| Construct/domain | Depression severity |
| Self-report / clinician-rated | **Clinician-rated** (structured interview, scored by a trained rater) — **not a self-report instrument** |
| Target population | Adults with a depression diagnosis already established, used to track severity/treatment response |
| Age range | metadata incomplete |
| Reference period | Typically past week, at the time of interview |
| Item count | 17 (original) or 21 (extended version) |
| Subscales | None in the standard 17-item form; some extended versions separate items |
| Score direction | Higher = more severe |
| Known risk-sensitive item | Yes — includes a suicide item, rated by the interviewer, not self-administered |
| Primary/official source | Hamilton M. (1960). "A rating scale for depression." *J Neurol Neurosurg Psychiatry*, 23(1), 56–62 |
| Russian adaptation | Widely used in Russian clinical/research practice; specific licensed translation not verified in this PR |
| Copyright/license owner | Original 1960 publication; treated in much of the literature as freely available for clinical/research use, but this project does not treat that as verified legal clearance without separate confirmation |
| Digital reproduction status | metadata incomplete (not verified in this PR) |
| Commercial-use status | metadata incomplete |
| Translation-use status | metadata incomplete |
| Can be activated now? | **No — and not as an ordinary self-test even if rights were confirmed** |
| Evidence links | Owner-supplied: `https://psytests.org/diag/hdrs-run.html` (identification only) |
| Blocking uncertainty | Exact version; digital/commercial rights unverified; **structurally cannot become a Telegram self-test regardless of rights**, since it requires a trained rater conducting a structured interview |

**Classification: clinician-rated only.** This is not, and must never become, an ordinary user-facing self-test in this product. If ever integrated, the only honest product surface is metadata/information display (e.g. "this scale requires a trained specialist"), never a self-administered questionnaire flow.

---

## 3. Zung SAS / ZARS — Self-Rating Anxiety Scale

| Field | Value |
|---|---|
| Instrument ID | `zung_sas` |
| Exact full name | Zung Self-Rating Anxiety Scale |
| Abbreviation | SAS (also referenced as ZARS/Zung Anxiety Rating Scale in some sources) |
| Exact version | Single standard 20-item version; no major revision known |
| Author/developer | William W. K. Zung |
| Year | 1971 |
| Construct/domain | **Anxiety** (not depression) |
| Self-report / clinician-rated | Self-report |
| Target population | Adults |
| Age range | metadata incomplete |
| Reference period | Typically "recently" / past several days, per standard instructions |
| Item count | 20 |
| Subscales | None (unidimensional) |
| Score direction | Higher = more anxiety symptoms |
| Known risk-sensitive item | metadata incomplete — no confirmed suicide-specific item, unlike BDI/HDRS, but not independently verified item-by-item in this PR (and will not be, since items are not to be reproduced) |
| Primary/official source | Zung WW. (1971). "A rating instrument for anxiety disorders." *Psychosomatics*, 12(6), 371–379 |
| Russian adaptation | Exists in the literature; specific licensed version not verified |
| Copyright/license owner | metadata incomplete |
| Digital reproduction status | metadata incomplete |
| Commercial-use status | metadata incomplete |
| Translation-use status | metadata incomplete |
| Can be activated now? | **No** |
| Evidence links | Owner-supplied: `https://psytests.org/anxiety/zars.html` (identification only) |
| Blocking uncertainty | Rights status not verified |

**Classification: anxiety instrument.** **Must never be folded into a depression score or depression trend** — it measures a different construct than the depression-domain instruments below (BDI, Zung SDS, EPDS, DASS-Depression).

---

## 4. Zung SDS — Self-Rating Depression Scale

| Field | Value |
|---|---|
| Instrument ID | `zung_sds` |
| Exact full name | Zung Self-Rating Depression Scale |
| Abbreviation | SDS |
| Exact version | Single standard 20-item version |
| Author/developer | William W. K. Zung |
| Year | 1965 |
| Construct/domain | **Depression** |
| Self-report / clinician-rated | Self-report |
| Target population | Adults |
| Age range | metadata incomplete |
| Reference period | "Recently" / past several days, per standard instructions |
| Item count | 20 |
| Subscales | None (unidimensional) |
| Score direction | Higher = more depressive symptoms |
| Known risk-sensitive item | metadata incomplete |
| Primary/official source | Zung WW. (1965). "A Self-Rating Depression Scale." *Archives of General Psychiatry*, 12(1), 63–70 |
| Russian adaptation | Exists in the literature (sometimes cited as adapted by T.N. Balashova); specific licensed version not verified |
| Copyright/license owner | metadata incomplete |
| Digital reproduction status | metadata incomplete |
| Commercial-use status | metadata incomplete |
| Translation-use status | metadata incomplete |
| Can be activated now? | **No** |
| Evidence links | Owner-supplied: `https://psytests.org/depr/zung.html` (identification only) |
| Blocking uncertainty | Rights status not verified; scoring-key source not verified |

**Classification: depression self-report, possible future same-instrument trend only.** Explicitly a **separate instrument from Zung SAS** — different construct, different scale, must never be conflated or mixed in scoring/trends. A future depression trend for this instrument may only ever compare Zung SDS results against other Zung SDS results, once rights and a verified scoring source exist — never against BDI, EPDS, or DASS scores.

---

## 5. EPDS — Edinburgh Postnatal Depression Scale

| Field | Value |
|---|---|
| Instrument ID | `epds` |
| Exact full name | Edinburgh Postnatal Depression Scale |
| Abbreviation | EPDS |
| Exact version | Single standard 10-item version |
| Author/developer | J.L. Cox, J.M. Holden, R. Sagovsky |
| Year | 1987 |
| Construct/domain | Depressive symptoms specific to the **perinatal period** (pregnancy and postpartum) |
| Self-report / clinician-rated | Self-report |
| Target population | **Pregnant and postpartum individuals specifically** — not a general-population depression screen |
| Age range | Adult (childbearing age) |
| Reference period | Past 7 days |
| Item count | 10 |
| Subscales | None in standard scoring (an anxiety subscale has been proposed in later research but is not the original instrument) |
| Score direction | Higher = more depressive symptomatology |
| Known risk-sensitive item | **Yes** — item 10 directly asks about self-harm ideation. Per the owner's explicit requirement, any future activation of EPDS **must** route a risk-sensitive response through the existing crisis pipeline *before* computing or showing any total score — this is a hard product requirement, not optional. |
| Primary/official source | Cox JL, Holden JM, Sagovsky R. (1987). "Detection of postnatal depression: development of the 10-item Edinburgh Postnatal Depression Scale." *British Journal of Psychiatry*, 150, 782–786 |
| Russian adaptation | Exists in the literature; specific licensed version not verified |
| Copyright/license owner | Royal College of Psychiatrists (the original publisher); the instrument is broadly used in clinical/research practice with attribution, but this project does not treat broad clinical use as equivalent to confirmed commercial/digital-reproduction rights for a commercial product |
| Digital reproduction status | metadata incomplete |
| Commercial-use status | metadata incomplete — EPDS is frequently used free-of-charge for clinical screening with attribution, but this product's use case (a commercial-adjacent Telegram bot) has not been separately confirmed as within permitted use |
| Translation-use status | metadata incomplete |
| Can be activated now? | **No** |
| Evidence links | Owner-supplied: `https://psytests.org/depr/epds.html` (identification only) |
| Blocking uncertainty | Commercial/digital-use confirmation for this specific product context; population-applicability gate not yet built |

**Classification: perinatal-specific, not a generic depression test.** Must never be offered as a general depression screen. Any future implementation requires an explicit applicability question first ("does this fit your situation?") before the instrument is offered, and the risk-sensitive item must route to the existing crisis pipeline before scoring, per the owner's explicit requirement.

---

## 6. DASS — Depression Anxiety Stress Scales

| Field | Value |
|---|---|
| Instrument ID | `dass` (parent); specific versions `dass_21` / `dass_42` |
| Exact full name | Depression Anxiety Stress Scales |
| Abbreviation | DASS |
| Exact version | **Two distinct versions exist: DASS-42 (original, 42 items) and DASS-21 (short form, 21 items). Which one the source page reproduces was not verified in this PR — this is a required blocker, not a detail.** DASS-21 and DASS-42 use different scoring conventions (DASS-21 subscale scores are conventionally doubled to remain comparable to DASS-42 norms) and must never be treated as interchangeable. |
| Author/developer | Peter F. Lovibond, Sydney H. Lovibond (University of New South Wales) |
| Year | 1995 |
| Construct/domain | Three **separate** subscales: Depression, Anxiety, Stress |
| Self-report / clinician-rated | Self-report |
| Target population | Adults |
| Age range | metadata incomplete |
| Reference period | Past week |
| Item count | 42 (DASS-42) or 21 (DASS-21) |
| Subscales | Depression, Anxiety, Stress — **reported and tracked separately, never combined into one "mood" or "depression" number** |
| Score direction | Higher = more severe on each subscale |
| Known risk-sensitive item | metadata incomplete |
| Primary/official source | Lovibond SH, Lovibond PF. *Manual for the Depression Anxiety Stress Scales* (2nd ed.), Psychology Foundation of Australia, 1995; official instrument site: UNSW (University of New South Wales) |
| Russian adaptation | Exists in the literature; specific licensed version not verified |
| Copyright/license owner | Psychology Foundation of Australia / Lovibond |
| Digital reproduction status | The official DASS site has historically permitted free non-commercial clinical/research reproduction with attribution; **commercial use is explicitly restricted per the official terms** — this product's context has not been separately confirmed as non-commercial-qualifying |
| Commercial-use status | **permission required** |
| Translation-use status | metadata incomplete |
| Can be activated now? | **No** |
| Evidence links | Owner-supplied: `https://psytests.org/depr/dass.html` (identification only) |
| Blocking uncertainty | Exact version (21 vs 42) not established; commercial-use permission not confirmed for this product |

**Classification: version-ambiguous, blocked.** Depression, Anxiety, and Stress subscales must always be stored and displayed as three separate values, never merged into a single score or a generic "mood" number, once/if this instrument is ever activated.

---

## 7. JAPS

| Field | Value |
|---|---|
| Instrument ID | `japs` |
| Exact full name | **metadata incomplete — identity not established** |
| Abbreviation | JAPS |
| Exact version | unknown |
| Author/developer | unknown |
| Year | unknown |
| Construct/domain | unknown — the source URL path (`psytests.org/work/japs.html`) suggests an occupational/work-related construct, but this is inferred from the URL slug, not confirmed identity, and is explicitly **not** treated as sufficient identification per this PR's rules |
| Self-report / clinician-rated | unknown |
| Target population | unknown |
| Age range | unknown |
| Reference period | unknown |
| Item count | unknown |
| Subscales | unknown |
| Score direction | unknown |
| Known risk-sensitive item | unknown |
| Primary/official source | **not found** — a targeted search for "JAPS" as a named psychometric instrument did not return a confirmed match. Several similarly-abbreviated but distinct instruments exist (Job Anxiety Scale, Job Attitude Scale, Job Accommodation Scale — none of which are abbreviated "JAPS" in the literature located), which is exactly the kind of confusion this governance process exists to prevent. |
| Russian adaptation | unknown |
| Copyright/license owner | unknown |
| Digital reproduction status | not applicable — instrument not identified |
| Commercial-use status | not applicable |
| Translation-use status | not applicable |
| Can be activated now? | **No** |
| Evidence links | Owner-supplied: `https://psytests.org/work/japs.html` (identification attempt only; did not yield confirmed identity) |
| Blocking uncertainty | **Full identity unconfirmed.** Per the owner's explicit instruction, no meaning is inferred from the abbreviation/slug alone. |

**Classification: `metadata_incomplete`.** Not eligible for any further work (research, catalog inclusion beyond a placeholder, or activation) until identity is independently and reliably established.

---

## 8. STAS

| Field | Value |
|---|---|
| Instrument ID | `stas` |
| Exact full name | **metadata incomplete — genuine identity conflict found** |
| Abbreviation | STAS |
| Exact version | unknown |
| Author/developer | unknown for the specific instrument at the source URL |
| Year | unknown |
| Construct/domain | **unresolved conflict**: "STAS" most commonly refers in the psychometric literature to Spielberger's **State-Trait Anger Scale** (anger, 1980, University of South Florida) — an anger/emotion-regulation instrument, not a depression instrument. But the owner-supplied source URL is categorized under `psytests.org/depr/` (the site's depression category), which does not match that identity. This is exactly the kind of mismatch this governance process is designed to catch rather than paper over. |
| Self-report / clinician-rated | unknown for the instrument actually at the source URL |
| Target population | unknown |
| Age range | unknown |
| Reference period | unknown |
| Item count | unknown |
| Subscales | unknown |
| Score direction | unknown |
| Known risk-sensitive item | unknown |
| Primary/official source | If the site's "STAS" is genuinely Spielberger's State-Trait Anger Scale, the primary source would be: Spielberger CD. *Preliminary Manual for the State-Trait Anger Scale (STAS)*, University of South Florida Human Resources Institute, 1980 — **but this cannot be confirmed as the correct identity for the depression-categorized page without further verification**, and is explicitly not assumed here. |
| Russian adaptation | unknown |
| Copyright/license owner | unknown |
| Digital reproduction status | not applicable — instrument identity unresolved |
| Commercial-use status | not applicable |
| Translation-use status | not applicable |
| Can be activated now? | **No** |
| Evidence links | Owner-supplied: `https://psytests.org/depr/stas.html` (identification attempt; produced a category/name mismatch rather than a confirmed identity) |
| Blocking uncertainty | **Category/name mismatch must be resolved before any further work.** Do not assume this is the anger scale merely because that's the most common "STAS"; do not assume it's a depression instrument merely because of the URL category. |

**Classification: `metadata_incomplete`, do not confuse with the anger/anxiety instrument of the same abbreviation.** Blocked pending independent identity resolution.

---

## Summary table

| ID | Domain | Self-report/clinician | Activation status | Primary blocker |
|---|---|---|---|---|
| `bdi_ii` | Depression | Self-report | `blocked` | License-gated; exact version unconfirmed |
| `hdrs` | Depression | **Clinician-rated** | `blocked` | Structurally not a self-test; rights unverified |
| `zung_sas` | Anxiety | Self-report | `blocked` | Rights unverified |
| `zung_sds` | Depression | Self-report | `blocked` | Rights unverified |
| `epds` | Depression (perinatal) | Self-report | `blocked` | Commercial-use confirmation; applicability gate not built |
| `dass` | Depression/Anxiety/Stress | Self-report | `blocked` | Exact version (21 vs 42) unconfirmed; commercial-use restricted |
| `japs` | unknown | unknown | `metadata_incomplete` | Identity not established |
| `stas` | unknown | unknown | `metadata_incomplete` | Identity conflict (anger vs depression-categorized page) |

**No instrument in this research pass can honestly be marked `ready`.** Every one requires further work — rights confirmation for the six identified instruments, and basic identity confirmation for JAPS and STAS — before any real content, scoring, or user-facing activation is possible.
