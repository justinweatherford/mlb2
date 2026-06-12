---
name: verify
description: Use when about to claim work is complete, fixed, or passing — requires running verification commands and confirming output before making any success claims
---

# Verification Before Completion

Claiming work is complete without verification is dishonesty, not efficiency.

**Core principle:** Evidence before claims, always.

**Violating the letter of this rule is violating the spirit of this rule.**

## The Iron Law

```
NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE
```

If you haven't run the verification command in this message, you cannot claim it passes.

## The Gate

Before claiming any status or expressing satisfaction:

1. **IDENTIFY** — What command proves this claim?
2. **RUN** — Execute the full command now (fresh, complete)
3. **READ** — Full output, check exit code, count failures
4. **VERIFY** — Does output confirm the claim?
   - If NO: State actual status with evidence
   - If YES: State the claim WITH evidence
5. **ONLY THEN** — Make the claim

Skip any step = asserting without evidence.

## Common Claims and What They Require

| Claim | Requires | Not Sufficient |
|-------|----------|----------------|
| Tests pass | Test command output: 0 failures | Previous run, "should pass" |
| Module imports work | `python test_logger_imports.py`: exit 0 | Code looks correct |
| App runs | `streamlit run app.py` loads without error | No visible Python errors |
| Bug fixed | Reproduce original symptom: now passes | Code changed, assumed fixed |
| API call works | Actual API response logged | No exception thrown |
| Scan completes | Full scan output visible | Partial output, no exception |

## Red Flags — Stop

- Using "should", "probably", "seems to"
- Expressing satisfaction before verification ("Great!", "Perfect!", "Done!")
- About to commit without running tests
- Trusting a subagent's "success" report without checking the diff
- Relying on partial verification ("imports worked so it should run")
- Thinking "just this once"

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Should work now" | Run the verification |
| "I'm confident" | Confidence ≠ evidence |
| "Just this once" | No exceptions |
| "Agent said success" | Verify independently by checking the actual output |
| "Partial check is enough" | Partial proves nothing |
| "Different words so rule doesn't apply" | Spirit over letter |

## Verification Patterns

**Module imports:**
```bash
python test_logger_imports.py
# Must see: "✅ All modules imported successfully!"
```

**Specific test:**
```bash
python tests/test_output_path_manager.py
# Must see: explicit pass/fail output, no exceptions
```

**App launches:**
```bash
streamlit run app.py
# Must see: "You can now view your Streamlit app in your browser"
# AND load the URL and confirm no crash on initial render
```

**API connectivity:**
```python
# Run a real minimal call, not just "import worked"
# Log the raw response before claiming "API works"
```

**TDD regression check:**
```
Write test → Run (must PASS) → Revert the fix → Run (must FAIL) → Restore fix → Run (must PASS)
```
A test that doesn't fail when the fix is removed is not a regression test.

## For This Project

Standard verification sequence before marking any task complete:

```bash
# 1. All module imports
python test_logger_imports.py

# 2. Relevant test file
python tests/<relevant_test>.py

# 3. If UI was changed: start app and visually confirm
streamlit run app.py
```

Before any commit:
- [ ] Ran test_logger_imports.py — all green
- [ ] Ran the specific test for what you changed — all green
- [ ] No new warnings in console output
- [ ] If UI changed: visually confirmed in browser

## The Bottom Line

Run the command. Read the output. Then claim the result.

This is non-negotiable.
