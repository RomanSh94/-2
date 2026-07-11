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

Version semantics: `ClinicalScorerKey.scoring_version` is the SINGLE
authoritative scorer revision. ClinicalScoreResult deliberately carries no
separate algorithm/revision field, so two revision fields can never disagree.
"""
import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence, runtime_checkable

import clinical_definition_validator as _cdv
import clinical_instrument_catalog as _cat

# Stable-token policy for identifiers appearing in scoring inputs/outputs
# (subscale keys, item/answer ids): bounded ASCII token, no whitespace/colon.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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
    complete ClinicalScorerKey — never a partial/fuzzy match.

    There is deliberately NO public .score() convenience method: executing a
    scorer without linkage/response/result validation would be a bypass around
    score_validated_clinical_definition(), which is the sole high-level
    entrypoint."""

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


def _is_finite_number(value) -> bool:
    """True for finite int/float; bool is explicitly NOT a number here."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


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


def _validated_definition_items(definition: dict) -> list[dict]:
    """Fail-closed structural check of the definition's items/options: unique,
    non-empty stable token ids, finite numeric option values. Never silently
    normalizes an invalid identifier."""
    items = definition.get("items", []) or []
    if not items:
        raise ClinicalScoringError("definition has no items")
    seen_item_ids: set[str] = set()
    for item in items:
        item_id = item.get("id")
        if not isinstance(item_id, str) or not _TOKEN_RE.fullmatch(item_id):
            raise ClinicalScoringError(
                f"definition item id is not a stable token: {item_id!r}")
        if item_id in seen_item_ids:
            raise ClinicalScoringError(f"duplicate definition item id {item_id!r}")
        seen_item_ids.add(item_id)
        seen_option_ids: set[str] = set()
        for option in item.get("options", []) or []:
            option_id = option.get("id")
            if not isinstance(option_id, str) or not _TOKEN_RE.fullmatch(option_id):
                raise ClinicalScoringError(
                    f"option id in item {item_id!r} is not a stable token: "
                    f"{option_id!r}")
            if option_id in seen_option_ids:
                raise ClinicalScoringError(
                    f"duplicate option id {option_id!r} in item {item_id!r}")
            seen_option_ids.add(option_id)
            try:
                option_value = float(option["value"])
            except (TypeError, ValueError, KeyError):
                raise ClinicalScoringError(
                    f"non-numeric option value in item {item_id!r}")
            if isinstance(option.get("value"), bool) or not math.isfinite(option_value):
                raise ClinicalScoringError(
                    f"non-finite option value in item {item_id!r}")
    return items


def validate_clinical_responses(
        definition: dict,
        responses: Sequence[ClinicalResponse]) -> tuple[ClinicalResponse, ...]:
    """Fail-closed completeness + integrity check. Every item answered exactly
    once, every response an actual ClinicalResponse whose item_id/answer_id/
    answer_value match the CURRENT definition by STABLE TOKEN id (never label,
    never position). Returns the responses in canonical item order. Never
    mutates its inputs."""
    items = _validated_definition_items(definition)
    if not responses:
        raise ClinicalScoringError("no responses")

    by_item: dict[str, ClinicalResponse] = {}
    valid_item_ids = {item["id"] for item in items}
    for r in responses:
        if not isinstance(r, ClinicalResponse):
            raise ClinicalScoringError(
                f"response must be a ClinicalResponse, got {type(r).__name__}")
        if not isinstance(r.item_id, str) or not _TOKEN_RE.fullmatch(r.item_id):
            raise ClinicalScoringError(
                f"response item_id is not a stable token: {r.item_id!r}")
        if not isinstance(r.answer_id, str) or not _TOKEN_RE.fullmatch(r.answer_id):
            raise ClinicalScoringError(
                f"response answer_id is not a stable token: {r.answer_id!r}")
        if not _is_finite_number(r.answer_value):
            raise ClinicalScoringError(
                f"answer_value for item {r.item_id!r} must be a finite number, "
                f"got {r.answer_value!r}")
        if r.item_id not in valid_item_ids:
            raise ClinicalScoringError(f"unknown item_id {r.item_id!r}")
        if r.item_id in by_item:
            raise ClinicalScoringError(f"duplicate response for item {r.item_id!r}")
        by_item[r.item_id] = r

    if len(by_item) != len(items):
        # Missing responses (extra/unknown ids already rejected above).
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
        if float(r.answer_value) != float(option["value"]):
            raise ClinicalScoringError(
                f"answer_value {r.answer_value!r} does not match option "
                f"{r.answer_id!r} value {option['value']!r}")
        ordered.append(r)
    return tuple(ordered)


def validate_clinical_score_result(
        result: object,
        *,
        expected_key: ClinicalScorerKey,
        expected_item_ids: tuple[str, ...]) -> ClinicalScoreResult:
    """Fail-closed validation of a scorer's OUTPUT (§4.2). A key match alone is
    insufficient: a buggy scorer could return the wrong type, NaN/infinity,
    booleans, missing/duplicate/misordered item ids, or a mutable shared
    mapping. Returns a defensively-copied, immutable-mapping result -- never
    the scorer-owned object graph."""
    if not isinstance(result, ClinicalScoreResult):
        raise ClinicalScoringError(
            f"scorer must return ClinicalScoreResult, got {type(result).__name__}")
    if result.scorer_key != expected_key:
        raise ClinicalScoringError(
            "scorer returned a result for a different scorer_key")
    # Item ids: exactly the validated set, in canonical definition order.
    if not isinstance(result.scored_item_ids, tuple):
        raise ClinicalScoringError("scored_item_ids must be a tuple")
    if len(set(result.scored_item_ids)) != len(result.scored_item_ids):
        raise ClinicalScoringError("duplicate scored_item_ids")
    if result.scored_item_ids != expected_item_ids:
        raise ClinicalScoringError(
            "scored_item_ids do not exactly match the validated responses "
            "in canonical definition order")
    # Numeric totals: finite int/float or None; bool rejected.
    for name, value in (("raw_total", result.raw_total),
                        ("transformed_total", result.transformed_total)):
        if value is not None and not _is_finite_number(value):
            raise ClinicalScoringError(
                f"{name} must be a finite number or None, got {value!r}")
    # Subscales: stable token keys, finite numeric values.
    if not isinstance(result.subscales, Mapping):
        raise ClinicalScoringError("subscales must be a mapping")
    for key, value in result.subscales.items():
        if not isinstance(key, str) or not _TOKEN_RE.fullmatch(key):
            raise ClinicalScoringError(
                f"subscale key is not a stable token: {key!r}")
        if not _is_finite_number(value):
            raise ClinicalScoringError(
                f"subscale {key!r} value must be a finite number, got {value!r}")
    # At least one numeric output must exist.
    if (result.raw_total is None and result.transformed_total is None
            and not result.subscales):
        raise ClinicalScoringError(
            "scorer produced no numeric output (raw_total, transformed_total "
            "and subscales are all empty)")
    # Defensive copy: never retain/return the scorer-owned mutable mapping.
    return ClinicalScoreResult(
        scorer_key=result.scorer_key,
        raw_total=result.raw_total,
        transformed_total=result.transformed_total,
        subscales=MappingProxyType(dict(result.subscales)),
        scored_item_ids=tuple(result.scored_item_ids))


def score_validated_clinical_definition(
        definition: dict,
        manifest_document: dict,
        responses: Sequence[ClinicalResponse],
        scorer_registry: ClinicalScorerRegistry) -> ClinicalScoreResult:
    """Pure orchestration. Order is load-bearing (see task §13): validate the
    manifest, require a VALID exact linkage, require self_report, reject a
    risk-bearing definition, derive the EXACT scorer key, require an exactly
    registered scorer, validate responses, execute (failures normalized to
    ClinicalScoringError), validate the RESULT, and return a defensively
    copied result. Data only -- no Telegram/DB/Registry/persistence, no
    interpretation."""
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

    # 5. Risk-bearing definitions are refused before the scorer is called (via
    # the PUBLIC shared predicate -- single implementation, never weakened).
    # Risk routing is a separate, exact-version concern (not implemented here).
    if _cdv.definition_is_risk_bearing(definition):
        raise ClinicalScoringError("risk-bearing definition is not scoreable here")

    # 6-7. Exact scorer key from the manifest; require an exactly registered
    # scorer (no default, no inference).
    key = _scorer_key_for(definition, manifest_document)
    scorer = scorer_registry.resolve(key)

    # 8. Response completeness/integrity (stable token ids only).
    ordered = validate_clinical_responses(definition, responses)

    # 9. Execute the vetted scorer; normalize arbitrary failures (§4.6): a
    # ClinicalScoringError passes through, anything else is wrapped fail-closed
    # with the original exception preserved as __cause__. No partial result.
    try:
        result = scorer.score(definition, ordered)
    except ClinicalScoringError:
        raise
    except Exception as exc:  # noqa: BLE001 -- normalize scorer bugs fail-closed
        raise ClinicalScoringError("scorer raised an unexpected error") from exc

    # 10-11. Validate the RESULT (key, item ids, finiteness, shape) and return
    # a defensively copied, immutable-mapping result. Data only.
    return validate_clinical_score_result(
        result, expected_key=key,
        expected_item_ids=tuple(r.item_id for r in ordered))
