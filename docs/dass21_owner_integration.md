# DASS-21 (Fattakhov RU) — owner-only integration (PR #55)

## Exact identity (never inferred)

| Field | Value |
|---|---|
| instrument_id | `dass` |
| instrument_version | `DASS-21` |
| translation_id | `fattakhov_ru_2024` |
| questionnaire_definition_id | `dass21_ru_fattakhov_2024` |
| scoring_contract_id | `dass21_official_subscales` |
| scoring_version | `unsw_template_v1` |
| risk_contract_id / risk_contract_version | `null` / `null` |
| administration_mode | `self_report` |
| language | `ru` |

Translation shown on the official PDF: **И.М. Фаттахов, Е.А. Горобец, 2024**
(authors: S. H. Lovibond, P. F. Lovibond, 1995).

## Official sources (all accessed 2026-07-11)

- https://www2.psy.unsw.edu.au/dass/ — "The DASS questionnaire is in the
  public domain, and may be downloaded from this website."
- https://www2.psy.unsw.edu.au/dass/down.htm — "The DASS questionnaire forms
  may be downloaded and copied without restriction." / "The scales may not be
  modified or sold for profit."
- https://www2.psy.unsw.edu.au/dass/over.htm — "none of the DASS items refers
  to suicidal tendencies" (basis for `contains_risk_items=false`, no risk
  contract); "a short version, the DASS21, is available with 7 items per
  scale."
- https://www2.psy.unsw.edu.au/dass/Russian/Russian.htm — lists the
  "Fattakhov translation of DASS and DASS21".
- https://www2.psy.unsw.edu.au/dass/Russian/Fattakhov/Fattakhov_DASS-21_rus.pdf
  — the exact source of every item and answer wording in the private file.
- https://www2.psy.unsw.edu.au/dass/Download%20files/Dass_template.pdf —
  official scoring template: subscale item assignment; "For short (21-item)
  version, multiply sum by 2."

Known limitation (recorded in the manifest evidence): UNSW hosts third-party
translations without assuring their adequacy or validity.

Conditions honoured: wording and scoring are not modified; DASS-21 is not sold
as a separate product and no separate per-test fee is charged.

## Private content — never in Git

The real definition lives at
`private_questionnaires/dass21_ru_fattakhov_2024.json` (the whole
`private_questionnaires/` directory is gitignored). The repo carries only a
synthetic-shape fixture. To install on a machine:

1. Recreate/copy the private JSON (exact Fattakhov wording, 21 items
   `dass21_01..dass21_21`, options `a0..a3` with values `0..3`).
2. `sha256sum private_questionnaires/dass21_ru_fattakhov_2024.json`
3. Set in `.env`:

```dotenv
DASS21_ENABLED=true            # default false
DASS21_OWNER_ONLY=true         # default true
DASS21_DEFINITION_PATH=private_questionnaires/dass21_ru_fattakhov_2024.json
DASS21_DEFINITION_SHA256=<hash from step 2>
```

Empty/malformed hash, missing file, hash mismatch, or wrong identity inside
the file all fail closed (`dass21_runtime.py`); there is no fallback to any
other DASS definition.

## Surface

- Owner-only command `/dass21` → existing `q:d` detail screen (never a direct
  session). All downstream steps use the existing q:s/q:a/q:b/q:p/q:x engine.
- `public_catalog_visible=false`: DASS never appears in the public catalog,
  self-observation, navigation, or command hints.
- Fresh gate (feature flag + owner + hash + identity + manifest linkage) is
  re-run before /dass21, q:d, q:s, every q:a, q:b, and result computation.
  Cancel (q:x) always remains available; pause (q:p) stays state-preserving.

## Scoring and result

`dass21_scorer.py` implements the official template exactly: per subscale, the
sum of its seven item values × 2. Output is three numbers (Депрессия /
Тревога / Стресс) plus a fixed non-diagnostic disclaimer — **no overall total,
no cutoffs, no severity labels, no diagnosis, no LLM involvement, and no score
persistence** (recomputed from owned stored responses on the completion
screen). Scoring runs through `clinical_scoring.score_validated_clinical_definition`
with an explicit registry containing only `Dass21Scorer`.
