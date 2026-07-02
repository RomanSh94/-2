"""PR 1A — psychologist_review_pack: privacy-controlled STRUCTURE/export shell.

The single most sensitive artifact in the system (aggregates raw messages,
responses, modes, patterns, reactions into one file for a live psychologist to
review). This module implements ONLY the container and its privacy contract:

  - generated ONLY on explicit owner request — nothing here is called
    automatically from the bot's message pipeline;
  - if saved to disk, ONLY under the gitignored `private_review_packs/` directory,
    with its own delete function;
  - NEVER passed to logs / admin alerts / webhooks / CI artifacts / debug output —
    this module must not import the alerting module or reference any
    alert/log-broadcast symbol (enforced by tests/test_review_pack.py, a static
    scan in the same style as the A1 default-deny guard).

No product content is wired in yet (no questionnaire/CBT/schema/pattern/Hard
Mirror modules exist). The shell fields for those are populated with None/empty
placeholders and a comment marking them as future-PR content. The one REAL,
already-existing data source wired in is `influence_trace` (PR 0) — proving the
mechanism actually connects to real data, not only a fake shape.
"""
import json
import pathlib
from datetime import datetime, timezone

REVIEW_PACK_DIR = pathlib.Path("private_review_packs")


async def generate_review_pack(uid: int) -> dict:
    """Build the review pack IN MEMORY. Does not write to disk, log, or alert
    anywhere. Caller decides whether/where to persist it (save_review_pack)."""
    import database

    influence_rows = await database.get_influence_trace_for_user(uid)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "user_id": uid,
        # -- structure only; content wired in future product PRs --
        "user_message": None,          # future PR: the specific message under review
        "bot_response": None,          # future PR: the specific bot reply under review
        "active_mode": None,           # future PR: Schema Pattern System (mode engine)
        "hard_mirror_state": None,     # future PR: Hard Mirror (A4)
        "hard_mirror_brake_result": None,  # future PR: hard_mirror_brake.py verdict
        "influence_trace": influence_rows,  # REAL — the one wired data source today
        "questionnaire_sources": [],   # future PR: Questionnaire Core
        "pattern_hypotheses": [],      # future PR: Schema Pattern System
        "proposed_experiment": None,   # future PR: Behavior experiments
        "user_reaction": None,         # future PR
        "needs_specialist_review": False,  # future PR: explicit flag from the flow
    }


def save_review_pack(pack: dict, uid: int) -> pathlib.Path:
    """Persist a generated pack to the gitignored private export directory ONLY.
    Never call this automatically — owner-request-only per the privacy contract."""
    REVIEW_PACK_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REVIEW_PACK_DIR / f"{uid}_{ts}.json"
    path.write_text(json.dumps(pack, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    return path


def delete_review_pack(path: pathlib.Path) -> None:
    """Delete a saved review pack file. Idempotent — a missing file is not an error."""
    p = pathlib.Path(path)
    if p.exists():
        p.unlink()
