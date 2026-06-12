---
name: parallel-agents
description: Use when facing 2+ independent tasks that can be worked on without shared state or sequential dependencies
---

# Dispatching Parallel Agents

## Overview

Delegate tasks to specialized agents with isolated context. By precisely crafting their instructions and context, you ensure they stay focused and succeed. Agents should never inherit your session's context or history — you construct exactly what they need. This also preserves your own context for coordination work.

**Core principle:** Dispatch one agent per independent problem domain. Let them work concurrently.

## When to Use

**Use when:**
- 3+ test files failing with different root causes
- Multiple subsystems broken independently
- Multiple modules need implementing and they don't share state
- Each problem can be understood without context from others

**Don't use when:**
- Failures are related (fixing one might fix others)
- Need to understand full system state first
- Agents would edit the same files
- You're still in exploratory debugging (use `/debug` first)

## Decision Flow

```
Multiple independent tasks?
  └─ Yes → Can they work without shared state?
              └─ Yes → Parallel dispatch
              └─ No  → Sequential agents
  └─ No  → Single agent handles all
```

## The Pattern

### 1. Identify Independent Domains

Group work by what's isolated:
- Module A: `places_finder.py` — no dependency on scan results
- Module B: `opportunity_scorer.py` — no dependency on places data
- Module C: `llm_router.py` — independent utility

Each domain is independent — fixing one doesn't affect the others.

### 2. Write Focused Agent Tasks

Each agent gets:
- **Specific scope:** One file or subsystem only
- **Clear goal:** Exactly what to produce
- **Constraints:** What NOT to change
- **Expected output:** What to return when done

### 3. Dispatch in Parallel

Use the `Agent` tool to spawn multiple agents simultaneously — they run concurrently.

### 4. Review and Integrate

When agents return:
1. Read each summary
2. Check for conflicts — did agents touch the same code?
3. Run full test suite
4. Integrate all changes

## Good Agent Prompt Structure

```markdown
Fix the 3 failing tests in tests/test_opportunity_scorer.py:

1. "test_score_no_website" — expects score of 95, gets 0
2. "test_score_partial_presence" — KeyError on 'gmb_claimed'
3. "test_score_full_presence" — AssertionError on threshold

Your task:
1. Read tests/test_opportunity_scorer.py and modules/opportunity_scorer.py
2. Identify root cause of each failure
3. Fix the scorer implementation (not the tests)
4. Run: python tests/test_opportunity_scorer.py

Do NOT change any other module.
Return: Summary of root cause and what you changed.
```

## Common Mistakes

| Wrong | Right |
|-------|-------|
| "Fix all the tests" | "Fix tests/test_opportunity_scorer.py only" |
| No error context | Paste the actual error messages |
| No constraints | "Do NOT change modules/places_finder.py" |
| Vague output | "Return: root cause + list of lines changed" |

## Verification After Integration

After all agents return:
1. Review each summary — understand what changed and why
2. Check for conflicts — did any two agents edit the same file?
3. Run full test suite: `python test_logger_imports.py` + all test files
4. Spot-check — agents can make systematic errors; verify a sample

## For This Project

IBR modules that are good parallel candidates (they don't share state at build time):
- `modules/places_finder.py` — Google Places API, independent
- `modules/website_scanner.py` — URL scanning, independent
- `modules/opportunity_scorer.py` — scoring logic, independent
- `modules/llm_router.py` — LLM dispatch, independent
- `modules/concept_engine.py` — narrative generation, independent
- `modules/full_audit_builder.py` — PDF assembly, independent

These are good sequential (not parallel) because of dependencies:
- Scanner must run before scorer (scorer needs scan results)
- Scorer must run before concept engine (narratives need scores)
- Concept engine must run before audit builder (PDF needs narratives)
