"""Questionnaire Registry (PR A) — multi-definition, in-chat UX skeleton.

Fully REPLACES the earlier Questionnaire Core PR #1 single-definition loader
(`get_validated_definition(directory)` returning a single dict). That loader
supported exactly one active private definition and refused outright on
ambiguity (zero files => not_configured, 2+ files => invalid). This module
generalizes to a directory of MANY definition files, each carrying its own
`status` (active/draft/archived/restricted). The old "more than one file is
ambiguous" rule no longer applies -- multiple files are the normal case now;
each is validated and keyed by its own `id`. See the module docstring in the
git history / PR description for the full behavioral-parity write-up.

This module does NOT compute scores, does NOT interpret answers, and does NOT
contain any real questionnaire text -- only synthetic/demo fixture content
(see tests/fixtures/*.json) is ever loaded in tests, and real/licensed
instrument text (if it ever exists) would live only under the gitignored
`private_questionnaires/` directory, never under tests/fixtures/.

Risk-item handling (unchanged guarantee from PR #1, still fail-closed, no
fuzzy matching): a definition is rejected outright if its top-level
`contains_risk_items` is true, or if any item or option carries
`risk_flag: true`. This PR does not route risk items into the crisis path --
it simply refuses to load a risk-bearing definition at all.
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

STATUSES = ("active", "draft", "archived")
LEGAL_STATUSES = ("public_domain", "licensed", "restricted", "synthetic")
RESULT_POLICIES = ("user_visible_full", "user_visible_score", "specialist_only", "no_score")

# Telegram callback_data has a 64-byte hard limit. Callback format (see
# CALLBACK FORMAT section below) embeds the option id directly
# ("q:a:<sid>:<step>:<aid>") -- validate option ids so a definition can never
# produce an unsendable/unsafe callback later. Item ids are NOT constrained
# the same way: they're stored in DB / read from session state, never placed
# into callback_data (the current item is derived from `step`, not encoded).
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

    status = d.get("status", "active")
    if status not in STATUSES:
        raise DefinitionError(f"invalid status: {status!r}")

    legal_status = d.get("legal_status", "synthetic")
    if legal_status not in LEGAL_STATUSES:
        raise DefinitionError(f"invalid legal_status: {legal_status!r}")

    result_policy = d.get("result_policy", "no_score")
    if result_policy not in RESULT_POLICIES:
        raise DefinitionError(f"invalid result_policy: {result_policy!r}")

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


def _normalize(d: dict) -> dict:
    """Applies documented defaults. Returns a NEW dict; never mutates the
    caller's dict in place (callers may reuse the same parsed object)."""
    out = dict(d)
    out.setdefault("status", "active")
    out.setdefault("legal_status", "synthetic")
    out.setdefault("result_policy", "no_score")
    out.setdefault("requires_gender", False)
    out.setdefault("requires_age", False)
    out.setdefault("age_ranges", [])
    out.setdefault("allow_skip_age", True)
    out.setdefault("category", None)
    out.setdefault("estimated_minutes", None)
    out.setdefault("description_short", out.get("description", ""))
    return out


class Registry:
    """Loads and validates every *.json file in `directory`. Fail-closed per
    file: a malformed or risk-bearing file is simply excluded (never crashes
    the whole registry, and never falls back to running it unvalidated).

    `by_id` only contains definitions that passed validation, keyed by `id`.
    Status (active/draft/archived/restricted-via-legal_status) is preserved
    on the definition itself -- callers (bot.py) enforce what each status may
    do; this module does not decide UX policy, only load/validate data.
    """

    def __init__(self, directory: str | pathlib.Path = PRIVATE_QUESTIONNAIRES_DIR):
        self.directory = pathlib.Path(directory)
        self.by_id: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.directory.exists():
            return
        for path in sorted(self.directory.glob("*.json")):
            try:
                candidate = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            try:
                _validate_definition(candidate)
            except DefinitionError:
                continue
            self.by_id[candidate["id"]] = _normalize(candidate)

    # ── read helpers ─────────────────────────────────────────────────────
    def get(self, qid: str) -> dict | None:
        return self.by_id.get(qid)

    def list_active(self, category: str | None = None) -> list[dict]:
        """Definitions that are listed and startable: status == active. Does
        NOT filter by legal_status -- "restricted" is a separate, orthogonal
        gate enforced at start/answer time (see can_start/can_answer), not at
        listing time, so future UX (e.g. "coming soon") could still show it.
        In THIS PR, callers additionally hide restricted from listings too;
        see bot.py's category-screen builder."""
        out = [d for d in self.by_id.values() if d.get("status") == "active"]
        if category is not None:
            out = [d for d in out if d.get("category") == category]
        return out

    def can_start(self, qid: str) -> bool:
        d = self.by_id.get(qid)
        if d is None:
            return False
        if d.get("status") != "active":
            return False
        if d.get("legal_status") == "restricted":
            return False
        return True

    def can_answer(self, qid: str) -> bool:
        # Same fail-closed conditions as can_start -- re-checked independently
        # (not just cached) so a definition invalidated mid-session (archived,
        # set to draft/restricted, or made invalid) is caught on every answer,
        # not only at session start.
        return self.can_start(qid)


def load_registry(directory: str | pathlib.Path = PRIVATE_QUESTIONNAIRES_DIR) -> Registry:
    return Registry(directory)


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
