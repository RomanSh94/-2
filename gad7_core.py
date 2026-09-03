"""Exact-version GAD-7 core scoring contract and deterministic bands.

Pure module: no Telegram, DB, filesystem, network, LLM, diagnosis inference,
or crisis routing. The scorer executes only through the existing validated
clinical-scoring orchestration.
"""
from typing import Sequence

from clinical_scoring import (
    ClinicalResponse, ClinicalScoreResult, ClinicalScorerKey,
    ClinicalScoringError,
)

GAD7_DEFINITION_ID = "gad7_ru_zolotareva_2023"
GAD7_SCORER_KEY = ClinicalScorerKey(
    instrument_id="gad7",
    instrument_version="GAD-7",
    translation_id="zolotareva_ru_2023",
    scoring_contract_id="gad7_sum",
    scoring_version="core_v1",
)
GAD7_ITEM_IDS = tuple(f"gad7_{number:02d}" for number in range(1, 8))
GAD7_MAX_SCORE = 21

_BAND_LABELS_RU = {
    "minimal": "минимальная",
    "mild": "лёгкая",
    "moderate": "умеренная",
    "severe": "тяжёлая",
}


def is_gad7_definition_id(definition_id) -> bool:
    return definition_id == GAD7_DEFINITION_ID


def is_gad7_definition(definition) -> bool:
    return (isinstance(definition, dict)
            and is_gad7_definition_id(definition.get("id")))


def band_for_score(score: int) -> str:
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 21:
        raise ClinicalScoringError("GAD-7 score must be an integer in 0..21")
    if score <= 4:
        return "minimal"
    if score <= 9:
        return "mild"
    if score <= 14:
        return "moderate"
    return "severe"


def band_label_ru(score: int) -> str:
    return _BAND_LABELS_RU[band_for_score(score)]


class Gad7Scorer:
    """Simple sum of the seven validated 0..3 answers; no other output."""

    key = GAD7_SCORER_KEY

    def score(self, definition: dict,
              responses: Sequence[ClinicalResponse]) -> ClinicalScoreResult:
        if tuple(response.item_id for response in responses) != GAD7_ITEM_IDS:
            raise ClinicalScoringError(
                "GAD-7 requires the seven canonical responses in definition order")
        values = []
        for response in responses:
            value = response.answer_value
            if (isinstance(value, bool) or int(value) != value
                    or not 0 <= int(value) <= 3):
                raise ClinicalScoringError("GAD-7 answer must be an integer in 0..3")
            values.append(int(value))
        total = sum(values)
        if not 0 <= total <= GAD7_MAX_SCORE:
            raise ClinicalScoringError("GAD-7 total outside 0..21")
        return ClinicalScoreResult(
            scorer_key=self.key,
            raw_total=total,
            transformed_total=None,
            subscales={},
            scored_item_ids=tuple(response.item_id for response in responses),
        )
