"""DASS-21 recompute contract for the shared q:m:<sid> "discuss result" flow.

Dass21DiscussionAdapter is the real, wired implementation used by
bot._dass21_discuss_gate_and_load (topic reply) and bot.cb_questionnaire_result
(read-only back-to-result). It re-derives a bounded, data-minimized result
through the SAME validated clinical-scoring path the DASS-21 completion screen
already uses (clinical_scoring.score_validated_clinical_definition + the sole
registered Dass21Scorer). Never persists a score, never computes a total (the
scorer contract returns raw_total=None), never assigns a severity/diagnosis
label the scorer contract doesn't provide.

The generic (synthetic, non-DASS) q:m path is UNCHANGED -- it keeps using
bot._discuss_gate_and_load directly (questionnaires.is_result_eligible +
questionnaires.compute_sum_score). It does not need or use this module; no
generic adapter/Protocol/dispatcher is defined here, since nothing in this
codebase would call it (a narrower unified adapter refactor is a separate,
later PR, not part of Workstream B).
"""
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

import clinical_scoring
import dass21_access
import dass21_runtime
import dass21_scorer


@dataclass(frozen=True)
class DiscussionAuth:
    allowed: bool
    reason_code: str


@dataclass(frozen=True)
class DiscussionResult:
    """Bounded, data-minimized recompute output -- safe to hand to an LLM
    prompt-builder. Never carries raw answers, item text, answer labels, or a
    total/severity/diagnosis the underlying scorer contract doesn't provide."""
    subscales: Mapping[str, int]
    instrument_id: str
    instrument_version: str
    translation_id: str


class Dass21DiscussionAdapter:
    def supports(self, definition: dict) -> bool:
        return dass21_runtime.is_dass21_definition(definition)

    async def authorize(self, session: dict) -> DiscussionAuth:
        decision = await dass21_access.authorize_dass21_user(session["user_id"])
        return DiscussionAuth(decision.allowed, decision.reason_code)

    def recompute_result(self, definition: dict, manifest: dict,
                          responses: Sequence[dict], session: dict) -> Optional[DiscussionResult]:
        if session.get("status") != "completed":
            return None
        if not dass21_runtime.dass21_integrity_status().available:
            return None
        try:
            clinical_responses = [
                clinical_scoring.ClinicalResponse(r["item_id"], r["answer_id"], int(r["answer_value"]))
                for r in responses
            ]
        except (KeyError, TypeError, ValueError):
            return None  # malformed stored row -- fail closed, never a partial result
        registry = clinical_scoring.ClinicalScorerRegistry()
        registry.register(dass21_scorer.Dass21Scorer())
        try:
            result = clinical_scoring.score_validated_clinical_definition(
                definition, manifest, clinical_responses, registry)
        except clinical_scoring.ClinicalScoringError:
            return None
        try:
            subscales = {
                "depression": result.subscales["depression"],
                "anxiety": result.subscales["anxiety"],
                "stress": result.subscales["stress"],
            }
        except (KeyError, TypeError):
            return None  # scorer result missing an expected subscale key
        key = dass21_scorer.DASS21_SCORER_KEY
        return DiscussionResult(
            subscales=subscales,
            instrument_id=key.instrument_id,
            instrument_version=key.instrument_version,
            translation_id=key.translation_id)
