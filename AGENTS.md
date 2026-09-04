# X20 Agent Instructions

Before any substantial task, read `X20_PROJECT_CONSTITUTION.md` in full.

Use this precedence:

1. newest explicit owner decision;
2. `X20_PROJECT_CONSTITUTION.md` for TARGET product/psychological doctrine;
3. current code + tests for CURRENT implementation;
4. older documentation only if non-conflicting.

Always label mentally and in reports:

CURRENT
TARGET
PROPOSED
SUPERSEDED

Do not:
- collapse X20 back into a generic emotional-support chatbot;
- treat TARGET as already implemented;
- add/remove CORE methods without owner approval;
- change architecture, memory semantics, safety ownership, intervention
  lifecycle, database architecture, model/provider calls, or major subsystem
  ownership without owner approval;
- assume a method/source in the KB authorizes autonomous protocol delivery;
- invent diagnoses, history, motives, schemas, or hidden causes as facts.

For user-facing psychological work preserve the Constitution's:
- one-deep-question-at-a-time rule;
- evidence-before-interpretation rule;
- method-explanation contract;
- longitudinal continuity;
- Telegram/MAX visual hierarchy contract.

Then inspect the exact code relevant to the task before making implementation
claims.

Do not add volatile PR/branch/SHA status to AGENTS.md.
