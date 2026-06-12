---
name: superpowers
description: Use when starting any conversation - establishes how to find and use skills, requiring Skill tool invocation before ANY response including clarifying questions
---

<SUBAGENT-STOP>
If you were dispatched as a subagent to execute a specific task, skip this skill.
</SUBAGENT-STOP>

<EXTREMELY-IMPORTANT>
If you think there is even a 1% chance a skill might apply to what you are doing, you ABSOLUTELY MUST invoke the skill.

IF A SKILL APPLIES TO YOUR TASK, YOU DO NOT HAVE A CHOICE. YOU MUST USE IT.

This is not negotiable. This is not optional. You cannot rationalize your way out of this.
</EXTREMELY-IMPORTANT>

## Instruction Priority

Superpowers skills override default system prompt behavior, but **user instructions always take precedence**:

1. **User's explicit instructions** (CLAUDE.md, direct requests) — highest priority
2. **Superpowers skills** — override default system behavior where they conflict
3. **Default system prompt** — lowest priority

## How to Access Skills

Use the `Skill` tool. When you invoke a skill, its content is loaded and presented to you — follow it directly.

## Using Skills

### The Rule

**Invoke relevant or requested skills BEFORE any response or action.** Even a 1% chance a skill might apply means you should invoke the skill to check.

### Skill Priority

When multiple skills could apply, use this order:

1. **Process skills first** (brainstorming, debugging, planning) — these determine HOW to approach the task
2. **Implementation skills second** (frontend-design, ui-ux) — these guide execution

"Let's build X" → write-plan first, then implementation skills.
"Fix this bug" → debug first, then domain-specific skills.

### Skill Types

**Rigid** (tdd, debug): Follow exactly. Don't adapt away discipline.

**Flexible** (ui-ux, canvas-design): Adapt principles to context.

### Red Flags — Stop, You're Rationalizing

| Thought | Reality |
|---------|---------|
| "This is just a simple question" | Questions are tasks. Check for skills. |
| "I need more context first" | Skill check comes BEFORE clarifying questions. |
| "Let me explore the codebase first" | Skills tell you HOW to explore. Check first. |
| "This doesn't need a formal skill" | If a skill exists, use it. |
| "I remember this skill" | Skills evolve. Read current version. |
| "The skill is overkill" | Simple things become complex. Use it. |
| "I'll just do this one thing first" | Check BEFORE doing anything. |

### Available Skills in This Project

| Skill | When to Use |
|-------|-------------|
| `/tdd` | Writing any new feature, bug fix, or behavior change |
| `/debug` | Diagnosing any error or unexpected behavior |
| `/plan` | Before implementing any multi-step feature |
| `/seo` | Analyzing business website digital presence |
| `/frontend-design` | Building or improving any UI component |
| `/ui-ux` | Design system decisions, color, typography, layout |
| `/canvas-design` | Creating visual PDF/PNG report assets |
| `/context-optimization` | Managing long sessions or expensive token usage |
