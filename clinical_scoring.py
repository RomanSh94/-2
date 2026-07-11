"""Exact-version clinical scoring contract (PR #53 — pure).

A deterministic, fail-closed scoring contract for a FUTURE approved
exact-version clinical instrument. This module is PURE: no Telegram, no DB, no
filesystem read, no network, no LLM, no user-facing text, no persistence, no
mutable global default registry. It never interprets a score (no cutoffs, no
severity, no diagnosis) — it only turns a fully-validated set of responses into
raw numeric totals, and ONLY for a definition whose manifest linkage is already
VALID and whose EXACT scorer is explicitly registered.

Authorization identity (never inferred from title, filename, item count, or
instrument family):

    instrument_id + instrument_version + translation_id
    + scoring_contract_id + scoring_version
    + complete validated responses

The existing generic `questionnaires.compute_sum_score` is untouched and keeps
serving the current synthetic/nonclinical result flow. This module does not
replace or duplicate it; it is a separate, exact-version, manifest-linked,
scorer-registry-based path that is NOT wired to any user-facing surface.

Scope guardrails (see CLAUDE.md / task): no real instrument scorer, no real
formula, no real item content, no scoring_contract_id/scoring_version mapped to
any real ready instrument (the production registry is empty by default).
"""
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence, runtime_checkable

import clinical_definition_validator as _cdv
import clinical_instrument_catalog as _cat


class ClinicalScoringError(ValueError):
    """Raised whenever scoring cannot proceed exactly. Callers MUST fail closed
    (no partial score, no guess, no user-facing disclosure of the reason)."""


@dataclass(frozen=True)
class ClinicalResponse:
    item_id: str
    answer_id: str
    answer_value: int | float


@dataclass(frozen=True)
class ClinicalScorerKey:
    instrument_id: str
    instrument_version: str
    translation_id: str
    scoring_contract_id: str
    scoring_version: str


@dataclass(frozen=True)
class ClinicalScoreResult:
    scorer_key: ClinicalScorerKey
    raw_total: int | float | None
    transformed_total: int | float | None
    subscales: Mapping[str, int | float]
    scored_item_ids: tuple[str, ...]
    algorithm_version: str


@runtime_checkable
class ClinicalScorer(Protocol):
    key: ClinicalScorerKey

    def score(self, definition: dict,
              responses: Sequence[ClinicalResponse]) -> ClinicalScoreResult:
        ...


class ClinicalScorerRegistry:
    """Explicit, per-instance scorer registry. There is deliberately NO module
    level default/singleton registry: production code must construct an empty
    one and register only vetted exact-version scorers. Resolution is by the
    complete ClinicalScorerKey — never a partial/fuzzy match."""

    def __init__(self) -> None:
        self._scorers: dict[ClinicalScorerKey, ClinicalScorer] = {}

    def register(self, scorer: ClinicalScorer) -> None:
        key = scorer.key
        if not isinstance(key, ClinicalScorerKey):
            raise ClinicalScoringError("scorer.key must be a ClinicalScorerKey")
        if key in self._scorers:
            raise ClinicalScoringError(f"scorer already registered for key {key}")
        self._scorers[key] = scorer

    def resolve(self, key: ClinicalScorerKey) -> ClinicalScorer:
        scorer = self._scorers.get(key)
        if scorer is None:
            raise ClinicalScoringError(f"no scorer registered for key {key}")
        return scorer

    def score(self, key: ClinicalScorerKey, definition: dict,
              responses: Sequence[ClinicalResponse]) -> ClinicalScoreResult:
        return self.resolve(key).score(definition, responses)


def _mapped_ready_entry(definition: dict, manifest_document: dict) -> dict:
    """The single manifest entry whose questionnaire_definition_id EXACTLY
    equals this definition's id. Exactly one exists once linkage is VALID."""
    definition_id = definition.get("id")
    entries = [e for e in (manifest_document or {}).get("instruments", []) or []
               if isinstance(e, dict)
               and e.get("questionnaire_definition_id") == definition_id]
    if len(entries) != 1:
        raise ClinicalScoringError(
            f"expected exactly one manifest mapping for {definition_id!r}, "
            f"got {len(entries)}")
    return entries[0]


def _scorer_key_for(definition: dict, manifest_document: dict) -> ClinicalScorerKey:
    """Derive the EXACT scorer key from the manifest entry (authoritative) after
    linkage is VALID. Every field must be a non-empty token; a null scoring
    contract/version means the instrument is not scoreable yet -> fail closed."""
    entry = _mapped_ready_entry(definition, manifest_document)
    values = {
        "instrument_id": entry.get("instrument_id"),
        "instrument_version": entry.get("version"),
        "translation_id": entry.get("translation_id"),
        "scoring_contract_id": entry.get("scoring_contract_id"),
        "scoring_version": entry.get("scoring_version"),
    }
    for name, value in values.items():
        if not isinstance(value, str) or not value.strip():
            raise ClinicalScoringError(
                f"cannot derive scorer key: {name} is not a non-empty token "
                f"({value!r}) -- instrument is not scoreable")
    return ClinicalScorerKey(**values)


def validate_clinical_responses(
        definition: dict,
        responses: Sequence[ClinicalResponse]) -> tuple[ClinicalResponse, ...]:
    """Fail-closed completeness + integrity check. Every item answered exactly
    once, every response's item_id/answer_id/answer_value matching the CURRENT
    definition by STABLE TOKEN id (never label). Returns the responses in item
    order. Never mutates its inputs."""
    items = definition.get("items", []) or []
    if not items:
        raise ClinicalScoringError("definition has no items")
    if not responses:
        raise ClinicalScoringError("no responses")

    by_item: dict[str, ClinicalResponse] = {}
    valid_item_ids = {item["id"] for item in items}
    for r in responses:
        if r.item_id not in valid_item_ids:
            raise ClinicalScoringError(f"unknown item_id {r.item_id!r}")
        if r.item_id in by_item:
            raise ClinicalScoringError(f"duplicate response for item {r.item_id!r}")
        by_item[r.item_id] = r

    if len(by_item) != len(items):
        # Missing and/or extra responses. (Extra unknown ids already rejected
        # above; this catches count mismatch from missing items.)
        raise ClinicalScoringError(
            f"expected {len(items)} responses, got {len(by_item)}")

    ordered: list[ClinicalResponse] = []
    for item in items:
        r = by_item[item["id"]]
        option = next((o for o in item.get("options", [])
                       if o.get("id") == r.answer_id), None)
        if option is None:
            raise ClinicalScoringError(
                f"answer_id {r.answer_id!r} not in item {item['id']!r}")
        if isinstance(r.answer_value, bool) or not isinstance(
                r.answer_value, (int, float)):
            raise ClinicalScoringError(
                f"answer_value for item {item['id']!r} must be numeric, "
                f"got {r.answer_value!r}")
        try:
            option_value = float(option["value"])
        except (TypeError, ValueError, KeyError):
            raise ClinicalScoringError(
                f"non-numeric option value in item {item['id']!r}")
        if float(r.answer_value) != option_value:
            raise ClinicalScoringError(
                f"answer_value {r.answer_value!r} does not match option "
                f"{r.answer_id!r} value {option['value']!r}")
        ordered.append(r)
    return tuple(ordered)


def score_validated_clinical_definition(
        definition: dict,
        manifest_document: dict,
        responses: Sequence[ClinicalResponse],
        scorer_registry: ClinicalScorerRegistry) -> ClinicalScoreResult:
    """Pure orchestration. Order is load-bearing (see task §13): validate the
    manifest, require a VALID exact linkage, require self_report, reject a
    risk-bearing definition, derive the EXACT scorer key, require an exactly
    registered scorer, validate responses, execute, and require the returned
    key to equal the requested key. Data only -- no Telegram/DB/Registry/
    persistence, no interpretation."""
    # 1-3. Manifest + exact linkage must be VALID (this also enforces exact
    # scoring-contract/version match between definition metadata and manifest).
    validation = _cdv.validate_clinical_definition_link(definition, manifest_document)
    if validation.status != _cdv.ClinicalDefinitionStatus.VALID:
        raise ClinicalScoringError(
            f"clinical linkage not VALID (status={validation.status.value})")

    # 4. Ordinary-product scoring is self_report only. A clinician_rated (or any
    # other) mode is refused here, before any scorer is touched.
    meta = definition.get(_cdv.CLINICAL_METADATA_KEY) or {}
    if meta.get("administration_mode") != "self_report":
        raise ClinicalScoringError(
            "only self_report definitions are scoreable in this product")

    # 5. Risk-bearing definitions are refused before the scorer is called. Risk
    # routing is a separate, exact-version concern (not implemented here).
    if _cdv._definition_is_risk_bearing(definition):
        raise ClinicalScoringError("risk-bearing definition is not scoreable here")

    # 6-7. Exact scorer key from the manifest; require an exactly registered
    # scorer (no default, no inference).
    key = _scorer_key_for(definition, manifest_document)
    scorer = scorer_registry.resolve(key)

    # 8. Response completeness/integrity (stable token ids only).
    ordered = validate_clinical_responses(definition, responses)

    # 9. Execute the vetted scorer.
    result = scorer.score(definition, ordered)

    # 10. The scorer must not silently answer for a different key.
    if result.scorer_key != key:
        raise ClinicalScoringError(
            "scorer returned a result for a different scorer_key")

    # 11. Data only.
    return result
