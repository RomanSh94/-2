"""Exact DASS-21 scorer (PR #55) — official UNSW template mapping.

Implements the single vetted scorer for the exact identity

    instrument_id=dass, instrument_version=DASS-21,
    translation_id=fattakhov_ru_2024,
    scoring_contract_id=dass21_official_subscales,
    scoring_version=unsw_template_v1

per the official UNSW scoring template (Dass_template.pdf, accessed
2026-07-11): sum the seven item values of each subscale and, for the 21-item
version, multiply the sum by 2. Returns the three subscale scores ONLY — no
overall total, no cutoffs, no severity labels, no percentiles, no diagnosis,
no interpretation of any kind (that is a clinician's job, never this bot's).

Pure and deterministic: no Telegram, no DB, no filesystem, no network, no LLM,
no mutable global registry. Executed exclusively through
clinical_scoring.score_validated_clinical_definition(), which validates the
manifest linkage, the responses AND this scorer's result before returning it.
"""
from typing import Sequence

from clinical_scoring import (
    ClinicalResponse, ClinicalScoreResult, ClinicalScorerKey,
    ClinicalScoringError,
)

DASS21_SCORER_KEY = ClinicalScorerKey(
    instrument_id="dass",
    instrument_version="DASS-21",
    translation_id="fattakhov_ru_2024",
    scoring_contract_id="dass21_official_subscales",
    scoring_version="unsw_template_v1",
)

# Official UNSW template item assignment (S A D A D S A S A D S S D S A D D S
# A A D for items 1..21). Exactly 7 items per subscale.
STRESS_ITEMS = ("dass21_01", "dass21_06", "dass21_08", "dass21_11",
                "dass21_12", "dass21_14", "dass21_18")
ANXIETY_ITEMS = ("dass21_02", "dass21_04", "dass21_07", "dass21_09",
                 "dass21_15", "dass21_19", "dass21_20")
DEPRESSION_ITEMS = ("dass21_03", "dass21_05", "dass21_10", "dass21_13",
                    "dass21_16", "dass21_17", "dass21_21")

_ALL_ITEMS = tuple(f"dass21_{n:02d}" for n in range(1, 22))
# DASS-21 subscale sums are multiplied by 2 (official template rule).
_DASS21_MULTIPLIER = 2


class Dass21Scorer:
    """The only DASS scorer. Registered explicitly per call site — the
    production scorer registry has no default/global instance."""

    key = DASS21_SCORER_KEY

    def score(self, definition: dict,
              responses: Sequence[ClinicalResponse]) -> ClinicalScoreResult:
        by_item = {r.item_id: r for r in responses}
        if len(by_item) != len(responses):
            raise ClinicalScoringError("duplicate item in responses")
        if set(by_item) != set(_ALL_ITEMS):
            raise ClinicalScoringError(
                "DASS-21 requires exactly the 21 canonical item responses")

        def subscale(item_ids: tuple[str, ...]) -> int:
            return _DASS21_MULTIPLIER * sum(
                int(by_item[i].answer_value) for i in item_ids)

        return ClinicalScoreResult(
            scorer_key=self.key,
            raw_total=None,          # deliberately no overall total
            transformed_total=None,
            subscales={
                "depression": subscale(DEPRESSION_ITEMS),
                "anxiety": subscale(ANXIETY_ITEMS),
                "stress": subscale(STRESS_ITEMS),
            },
            scored_item_ids=tuple(r.item_id for r in responses),
        )
