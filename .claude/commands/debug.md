# Systematic Debugging

**Core law: NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST.**

Symptom fixes are failure. Random fixes waste time and create new bugs by masking underlying issues.

## When to Use

Any time you encounter:
- An error or exception
- Unexpected behavior
- A test that won't pass
- A feature that stopped working

## The Four Required Phases

Complete these in order. Do not skip ahead.

### Phase 1: Root Cause Investigation

Before writing a single line of fix code:

1. **Read the error message completely** — every word, every line of the traceback
2. **Reproduce consistently** — if you can't reproduce it, you can't fix it
3. **Check recent changes** — what changed since it last worked? (`git diff`, `git log`)
4. **Gather evidence at boundaries** — what goes in, what comes out, where does the bad value originate?
5. **Trace data flow backward** — follow the bad value upstream through the call stack

Do not stop at Phase 1 until you can articulate: "The root cause is X because I observed Y."

### Phase 2: Pattern Analysis

1. Find a working example of the same pattern in the codebase
2. Compare against the broken code **completely** — every line, every argument
3. Identify all differences, however minor
4. Understand all dependencies and assumptions the working version relies on

### Phase 3: Hypothesis and Testing

1. Form **one specific hypothesis** about the root cause
2. Test it with the **smallest possible change** — one variable at a time
3. Verify the result explicitly
4. If the hypothesis is wrong, form a new one — do not pile fixes

### Phase 4: Implementation

1. Write a failing test that reproduces the bug
2. Implement only the root cause fix
3. Verify the test passes
4. Verify no other tests break

## Red Flags — STOP Immediately

You are abandoning the process if you catch yourself:

- Proposing a "quick fix" before completing Phase 1
- Changing more than one thing at a time
- Skipping test creation
- Saying "let me just try X and see"
- Making a third fix attempt without stopping to question the architecture

**After 3 failed fix attempts: stop.** The architecture itself may be the problem. Do not add more patches.

## Practical Rules

| Situation | Action |
|-----------|--------|
| Error in module import | Read the full traceback, find the originating line |
| API call returns wrong data | Log the raw response before any processing |
| UI state not updating | Trace the data from source through all transforms to render |
| Test passes but feature broken | The test is testing the wrong thing — rewrite it |
| Works on my machine | Find the environmental difference, not a code workaround |

## For This Project

Key debugging entry points:

```bash
# Test all module imports
python test_logger_imports.py

# Run the app with visible console output
streamlit run app.py

# Check logs
cat logs/  # check log files for errors
```

Common failure zones in this codebase:
- `modules/scan_orchestrator.py` — coordinates the full scan pipeline
- `modules/places_finder.py` — Google Places API calls
- `modules/llm_router.py` — OpenAI/Claude API routing
- `config/.env` — missing or malformed API keys

## Systematic Is Faster Than Thrashing

Real-world data: systematic debugging resolves issues in 15-30 minutes. Random guess-and-check takes 2-3 hours and has a 40% first-time fix rate. Systematic has a 95% first-time fix rate.

The feeling of "I should just try something" is the enemy. Resist it.
