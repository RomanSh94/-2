"""Exact-version clinical risk contract (PR #54 — pure, dormant).

A deterministic, fail-closed foundation for FUTURE safety routing of
risk-bearing clinical instruments. The current Questionnaire Core rejects
every risk-bearing definition (contains_risk_items / item.risk_flag /
option.risk_flag -> non-startable) and this module does NOT weaken that: it
never authorizes a runtime start, never calls the Registry, never calls the
existing crisis system (trigger_crisis / journal_guard), never sends anything.
It only defines and validates the exact contract:

    instrument_id + exact instrument_version + exact translation_id
    + risk_contract_id + risk_contract_version
    + exact stable item_id + exact stable answer_id
    = deterministic CRISIS action

Nothing is EVER inferred from question text, answer text, position/index,
instrument title, instrument family, item count, or substring matching —
matching is by exact stable token identity only.

This module is PURE: no Telegram, no DB, no filesystem, no network, no LLM,
no process-global mutable registry, no user-facing message, no alert delivery,
no severity tiers, no interpretation. A later separately reviewed PR may route
a CRISIS decision into the EXISTING crisis pipeline — never a new one.
"""
import re
from dataclasses import dataclass
from enum import Enum

import clinical_definition_validator as _cdv

# Stable-token policy (same as the manifest contract tokens): bounded ASCII,
# no whitespace, no colon.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# The ONLY action this PR defines. No severity, no free-form message, no
# destination. Non-trigger answers are deterministic NONE.
_ACTION_ALLOWLIST = ("crisis",)

RISK_CONTRACT_KEY = "clinical_risk_contract"


class ClinicalRiskContractError(ValueError):
    """Raised whenever the risk contract cannot be validated exactly. Callers
    MUST fail closed — no partial contract, no guessed trigger, no message."""


class ClinicalRiskAction(str, Enum):
    NONE = "none"
    CRISIS = "crisis"


@dataclass(frozen=True)
class ClinicalRiskContractKey:
    instrument_id: str
    instrument_version: str
    translation_id: str
    risk_contract_id: str
    risk_contract_version: str


@dataclass(frozen=True)
class ClinicalRiskTrigger:
    item_id: str
    answer_id: str
    action: ClinicalRiskAction


@dataclass(frozen=True)
class ClinicalRiskDecision:
    key: ClinicalRiskContractKey
    action: ClinicalRiskAction
    item_id: str
    answer_id: str


def _require_token(value, what: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise ClinicalRiskContractError(f"{what} is not a stable token: {value!r}")
    return value


def _mapped_entry(definition: dict, manifest_document: dict) -> dict:
    definition_id = definition.get("id")
    entries = [e for e in (manifest_document or {}).get("instruments", []) or []
               if isinstance(e, dict)
               and e.get("questionnaire_definition_id") == definition_id]
    if len(entries) != 1:
        raise ClinicalRiskContractError(
            f"expected exactly one manifest mapping for {definition_id!r}, "
            f"got {len(entries)}")
    return entries[0]


def _linkage_acceptable(definition: dict, manifest_document: dict) -> None:
    """The exact clinical linkage must be governance-consistent. VALID is
    accepted. BLOCKED is accepted ONLY when the sole blocking reason is
    definition-risk-bearing: a risk-bearing definition can never be VALID under
    the loader (by design — it stays non-startable), yet the risk contract
    exists precisely for such definitions. Every OTHER inconsistency
    (mismatch/INVALID, or any additional governance blocker) fails closed."""
    validation = _cdv.validate_clinical_definition_link(definition, manifest_document)
    if validation.status == _cdv.ClinicalDefinitionStatus.VALID:
        return
    if (validation.status == _cdv.ClinicalDefinitionStatus.BLOCKED
            and set(validation.reason_codes) == {"definition-risk-bearing"}):
        return
    raise ClinicalRiskContractError(
        f"clinical linkage not acceptable for a risk contract "
        f"(status={validation.status.value}, reasons={validation.reason_codes})")


def validate_clinical_risk_contract(
        definition: dict,
        manifest_document: dict) -> ClinicalRiskContractKey:
    """Fail-closed validation of the full exact-version risk contract. Order is
    load-bearing (task §15). Does NOT authorize runtime start, does NOT call
    the Registry, does NOT call the crisis system — it returns the exact key
    and nothing else."""
    # 1-3. Manifest + exact clinical linkage (instrument/version/translation
    # and 4. risk_contract_id/version exact match are enforced inside the
    # linkage validator; a mismatch there is INVALID and rejected here).
    _linkage_acceptable(definition, manifest_document)
    entry = _mapped_entry(definition, manifest_document)

    key_values = {
        "instrument_id": entry.get("instrument_id"),
        "instrument_version": entry.get("version"),
        "translation_id": entry.get("translation_id"),
        "risk_contract_id": entry.get("risk_contract_id"),
        "risk_contract_version": entry.get("risk_contract_version"),
    }
    for name, value in key_values.items():
        if not isinstance(value, str) or not value.strip():
            raise ClinicalRiskContractError(
                f"cannot derive risk contract key: {name} is not a non-empty "
                f"token ({value!r}) -- instrument has no configured risk contract")
        _require_token(value, name)

    # 5-6. The private definition must carry a clinical_risk_contract object
    # whose contract_id/version EXACTLY match the manifest pair.
    contract = definition.get(RISK_CONTRACT_KEY)
    if not isinstance(contract, dict):
        raise ClinicalRiskContractError(
            "definition has no clinical_risk_contract object")
    if contract.get("contract_id") != key_values["risk_contract_id"]:
        raise ClinicalRiskContractError("risk contract_id mismatch")
    if contract.get("contract_version") != key_values["risk_contract_version"]:
        raise ClinicalRiskContractError("risk contract_version mismatch")

    # 7-11. Trigger validation: exact stable ids, allowlisted action, unique
    # pairs, item exists, answer belongs to that item. Matching is NEVER by
    # label/text/position/value.
    triggers = contract.get("triggers")
    if not isinstance(triggers, list) or not triggers:
        raise ClinicalRiskContractError("risk contract has no triggers")
    items_by_id = {}
    for item in definition.get("items", []) or []:
        item_id = item.get("id")
        if not isinstance(item_id, str) or item_id in items_by_id:
            raise ClinicalRiskContractError(
                f"definition item ids must be unique stable tokens: {item_id!r}")
        items_by_id[item_id] = item
    seen_pairs: set[tuple[str, str]] = set()
    for trigger in triggers:
        if not isinstance(trigger, dict):
            raise ClinicalRiskContractError("trigger must be an object")
        item_id = _require_token(trigger.get("item_id"), "trigger item_id")
        answer_id = _require_token(trigger.get("answer_id"), "trigger answer_id")
        action = trigger.get("action")
        if action not in _ACTION_ALLOWLIST:
            raise ClinicalRiskContractError(
                f"trigger action must be one of {_ACTION_ALLOWLIST}, got {action!r}")
        pair = (item_id, answer_id)
        if pair in seen_pairs:
            raise ClinicalRiskContractError(f"duplicate trigger pair {pair!r}")
        seen_pairs.add(pair)
        item = items_by_id.get(item_id)
        if item is None:
            raise ClinicalRiskContractError(
                f"trigger item {item_id!r} does not exist in the definition")
        option_ids = {o.get("id") for o in item.get("options", []) or []}
        if answer_id not in option_ids:
            raise ClinicalRiskContractError(
                f"trigger answer {answer_id!r} does not belong to item {item_id!r}")

    # 12. Return the exact key. Nothing else. No start authorization.
    return ClinicalRiskContractKey(**key_values)


def contract_triggers(definition: dict) -> tuple[ClinicalRiskTrigger, ...]:
    """The declared triggers as immutable records (post-validation helper —
    call validate_clinical_risk_contract first for the full gate)."""
    contract = definition.get(RISK_CONTRACT_KEY) or {}
    return tuple(
        ClinicalRiskTrigger(t["item_id"], t["answer_id"],
                            ClinicalRiskAction(t["action"]))
        for t in contract.get("triggers", []) or [])


def evaluate_clinical_risk_answer(
        definition: dict,
        manifest_document: dict,
        *,
        item_id: str,
        answer_id: str) -> ClinicalRiskDecision:
    """Deterministic decision for ONE (item_id, answer_id) pair. Validates the
    full contract first, then matches by exact stable token identity only:
    CRISIS for an exact configured trigger pair, NONE for any other VALID
    (existing) pair. Unknown/tampered ids fail closed with an error. Never
    inspects labels/question text, never infers from an answer's numeric
    value, never produces a message, never mutates its inputs."""
    key = validate_clinical_risk_contract(definition, manifest_document)

    _require_token(item_id, "item_id")
    _require_token(answer_id, "answer_id")
    item = next((i for i in definition.get("items", []) or []
                 if i.get("id") == item_id), None)
    if item is None:
        raise ClinicalRiskContractError(f"unknown item_id {item_id!r}")
    if answer_id not in {o.get("id") for o in item.get("options", []) or []}:
        raise ClinicalRiskContractError(
            f"answer {answer_id!r} does not belong to item {item_id!r}")

    contract = definition[RISK_CONTRACT_KEY]
    is_trigger = any(
        t.get("item_id") == item_id and t.get("answer_id") == answer_id
        for t in contract.get("triggers", []))
    action = ClinicalRiskAction.CRISIS if is_trigger else ClinicalRiskAction.NONE
    return ClinicalRiskDecision(
        key=key, action=action, item_id=item_id, answer_id=answer_id)
