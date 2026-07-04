"""Questionnaire Core PR #1 — storage-only universal engine.

Loads questionnaire DEFINITIONS (private, gitignored JSON files under
`private_questionnaires/*.json`) and validates them fail-closed. This module
does NOT compute scores, does NOT interpret answers, and does NOT contain any
real questionnaire text — real/licensed instrument text is never committed to
this repo (see .gitignore's `private_questionnaires/` entry from PR 1A).

Risk-item handling (explicit, minimal, no fuzzy matching): a definition is
rejected outright if its top-level `contains_risk_items` is true, or if any
item or option carries `risk_flag: true`. This PR does NOT route risk items
into the crisis path -- it simply refuses to run a risk-bearing questionnaire
at all, so no risk-bearing definition can be used until a dedicated,
separately-reviewed safety-integration PR exists.
"""
import json
import re
import pathlib

PRIVATE_QUESTIONNAIRES_DIR = pathlib.Path("private_questionnaires")

_REQUIRED_TOP_LEVEL_FIELDS = (
    "id", "title", "version", "lang", "description",
    "contains_risk_items", "items", "completion_message",
)
_REQUIRED_ITEM_FIELDS = ("id", "text", "options")
_REQUIRED_OPTION_FIELDS = ("id", "label", "value")

# Telegram callback_data has a 64-byte hard limit. The future callback format
# is "q:a:<session_id>:<answer_id>" -- validate answer (option) ids now so a
# private definition can never produce an unsendable/unsafe callback later.
# Item ids are NOT constrained the same way: they're stored in DB, never
# placed into callback_data.
MAX_ANSWER_ID_LEN = 32
ANSWER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class DefinitionError(Exception):
    """Raised by _validate_definition. Any raise here means 'do not run this
    questionnaire' -- callers must fail closed, never fall back to running an
    unvalidated definition."""


def _validate_definition(d: dict) -> None:
    for field in _REQUIRED_TOP_LEVEL_FIELDS:
        if field not in d:
            raise DefinitionError(f"missing required field: {field}")

    if d.get("contains_risk_items"):
        raise DefinitionError("risk-bearing definition (contains_risk_items=true)")

    items = d["items"]
    if not items:
        raise DefinitionError("definition has no items")

    for item in items:
        if item.get("risk_flag"):
            raise DefinitionError(f"risk-bearing item: {item.get('id')!r}")
        for field in _REQUIRED_ITEM_FIELDS:
            if not item.get(field):
                raise DefinitionError(f"invalid item (missing {field}): {item!r}")
        for option in item["options"]:
            if option.get("risk_flag"):
                raise DefinitionError(
                    f"risk-bearing option: {option.get('id')!r} in item {item.get('id')!r}")
            for field in _REQUIRED_OPTION_FIELDS:
                if not option.get(field):
                    raise DefinitionError(f"invalid option (missing {field}): {option!r}")
            option_id = option["id"]
            if len(option_id) > MAX_ANSWER_ID_LEN or not ANSWER_ID_RE.match(option_id):
                raise DefinitionError(
                    f"invalid option id (must be <= {MAX_ANSWER_ID_LEN} chars, "
                    f"match {ANSWER_ID_RE.pattern}): {option_id!r}")


def get_validated_definition(
        directory: str | pathlib.Path = PRIVATE_QUESTIONNAIRES_DIR) -> tuple[dict | None, str | None]:
    """Returns (definition, error_code). Strict fail-closed loader -- every
    ambiguous or unexpected situation returns "invalid", never a silent guess.

    error_code is one of:
      None              -- `definition` is validated and safe to run. Exactly
                            one *.json file existed, parsed, and validated.
      "not_configured"   -- the directory doesn't exist, or exists but
                            contains zero *.json files.
      "invalid"          -- ANY of: a *.json file failed to parse as JSON; more
                            than one *.json file exists (PR #1 supports only a
                            single active private definition -- selecting
                            "first file wins" would silently ignore the
                            others, which is exactly the kind of ambiguity
                            this loader must refuse rather than guess at);
                            the (single) file failed schema/risk validation.

    No file is ever silently skipped: a malformed file or a second file
    present are both treated as reasons to refuse outright, not reasons to
    fall back to some other file."""
    p = pathlib.Path(directory)
    if not p.exists():
        return None, "not_configured"

    json_files = sorted(p.glob("*.json"))
    if not json_files:
        return None, "not_configured"
    if len(json_files) > 1:
        return None, "invalid"

    try:
        candidate = json.loads(json_files[0].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, "invalid"

    try:
        _validate_definition(candidate)
    except DefinitionError:
        return None, "invalid"
    return candidate, None


def get_item(definition: dict, index: int) -> dict | None:
    items = definition["items"]
    if 0 <= index < len(items):
        return items[index]
    return None


def find_option(item: dict, answer_id: str) -> dict | None:
    for option in item.get("options", []):
        if option.get("id") == answer_id:
            return option
    return None
