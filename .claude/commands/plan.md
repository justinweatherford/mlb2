# Writing Plans

Create a structured implementation plan before writing any code for multi-step features.

## When to Use

- Any feature that touches more than one file
- Any feature that will take more than 15 minutes to implement
- Any refactor that changes public interfaces
- Before executing a complex debugging fix

## Announcement

When invoking this skill, say: "I'm using the writing-plans skill to create the implementation plan."

## Plan Location

Save to: `docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md`

## Required Plan Header

```markdown
## Goal
[One sentence: what this plan achieves]

## Architecture
[Which modules are involved and how they interact]

## Tech Stack
[Relevant libraries/APIs being used]
```

## Decomposition Rules

1. **Map file structure first** — list every file that will be created or modified, with its responsibility
2. **Single responsibility per file** — if a file does two things, split the plan
3. **Break multi-subsystem specs into separate plans** — one plan per concern
4. **Each step = 2-5 minutes of work** — if a step takes longer, split it

## Task Structure

Every task must include:
- Exact file path(s)
- Complete code (no placeholders)
- Precise shell commands
- TDD cycle: failing test → implementation → passing test → commit

**Zero placeholders allowed:** "TBD", "implement later", "add validation" — these invalidate the plan.

## TDD Cycle Per Task

```
[ ] Write failing test in tests/<module>_test.py
[ ] Run test, confirm it fails for the right reason
[ ] Implement the minimal code to pass
[ ] Run test, confirm it passes
[ ] Run full test suite, confirm nothing else broke
[ ] Commit
```

## Quality Checks Before Handoff

- [ ] Every step has exact file paths
- [ ] Every step has complete code (no "..." or "etc.")
- [ ] Type/method names are consistent across all steps
- [ ] No step references a function or type not yet defined
- [ ] Plan fully covers the spec — nothing is deferred

## Execution Handoff

After the plan is created, offer two execution modes:

1. **Subagent-Driven** — spawn a fresh subagent per task (recommended for complex work where context drift is a risk)
2. **Inline Execution** — complete all tasks in the current session (better for small plans under ~10 steps)

## For This Project

Plan files go in `docs/superpowers/plans/`. That directory may not exist yet — create it on first use.

Relevant module boundaries:
- `modules/places_finder.py` — discovery only
- `modules/website_scanner.py` — scanning only
- `modules/scan_orchestrator.py` — coordinates scan pipeline
- `modules/llm_router.py` — LLM API routing
- `modules/opportunity_scorer.py` — scoring logic
- `app.py` — Streamlit UI only, no business logic

When a plan involves LLM calls, check `modules/llm_router.py` first to understand the existing routing pattern before designing new prompts.
