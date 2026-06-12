# Executing Plans

Structured workflow for implementing a written development plan with built-in review checkpoints. Companion to `/plan`.

## Source

obra/superpowers — `skills/executing-plans/SKILL.md`

## When to Use

- After `/plan` produces a plan file in `docs/superpowers/plans/`
- When picking up a plan written in a previous session
- Any time you have a step-by-step implementation plan to work through

## Required Companion Skills

- `/plan` — creates the plan this skill executes
- `/tdd` — each task follows Red-Green-Refactor
- `/verify` — run before marking any task complete

## Phase 1 — Load and Review

1. Read the plan file completely
2. Review it critically — identify anything unclear, missing, or risky
3. Raise all concerns with your human partner **before writing any code**
4. If no blockers: create a task tracking list mirroring the plan's checkboxes
5. If blockers exist: stop and resolve them first

**Do not guess when blocked. Ask.**

## Phase 2 — Execute Tasks

Work through tasks sequentially, one at a time:

1. Mark the task **in progress**
2. Follow the steps in the plan **precisely** — do not improvise
3. Run every verification command specified in the plan step
4. Mark the task **complete** only after verification passes (see `/verify`)
5. Move to the next task

### Rules During Execution

- **Sequential only** — complete each task before starting the next, unless the plan explicitly calls for parallel work (see `/parallel-agents`)
- **No scope creep** — implement exactly what the plan specifies, nothing more
- **Stop on blockers** — missing dependency, failing test that shouldn't fail, unclear instruction → stop and ask
- **Never implement on `main`/`master`** without explicit user consent

### When a Task Fails

If a task step fails:
1. Do not move forward
2. Do not patch around the failure
3. Apply `/debug` to find the root cause
4. If the plan itself has an error, flag it and ask for a plan amendment before continuing

## Phase 3 — Complete

After all tasks are verified complete:
1. Run the full test suite one final time
2. Confirm output is clean — no errors, no warnings
3. Transition to branch finalization (commit, PR, or merge as appropriate)

## Task Tracking Format

```markdown
## Tasks
- [x] Task 1: Write failing test for PlacesFinder.search()
- [x] Task 2: Implement PlacesFinder.search()
- [ ] Task 3: Write failing test for opportunity scorer
- [ ] Task 4: Implement opportunity scorer
```

Update checkboxes as you go. Never mark complete without running verification.

## For This Project

Plan files live in `docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md`.

Common execution patterns for IBR:
- New module → write test in `tests/`, implement in `modules/`, run `python tests/<file>.py`
- UI change → run `streamlit run app.py`, test in browser, confirm visually
- LLM prompt change → test with a real scan target, confirm output quality before committing
