# Canvas Design

Create sophisticated visual art in PNG and PDF formats using established design philosophies. Used for generating premium teaser reports and visual assets in Invisible Business Rescue.

## Source

Anthropic Skills — `anthropics/skills/blob/main/skills/canvas-design/SKILL.md`

## When to Use

- Generating the premium teaser PDF reports for target businesses
- Creating visual cover pages or summary layouts
- Designing any single-page artifact that must appear museum-quality
- Any output where visual communication should dominate over text

## Core Process

### Phase 1: Design Philosophy Creation

Develop an aesthetic movement expressed in 4-6 paragraphs that articulates:
- Form and space relationships
- Color philosophy
- Composition approach
- Visual hierarchy principles
- Material or textural qualities

The philosophy functions as a visual manifesto. It guides all subsequent design decisions. **"Information lives in design, not paragraphs."**

### Phase 2: Canvas Expression

Translate the philosophy into a single-page visual artifact (PDF or PNG) that:
- Is approximately **90% visual design, 10% essential text**
- Embodies the established aesthetic with minimal, purposeful typography
- Treats text as a visual element, not a communication vehicle
- Appears as though crafted over countless hours by someone at the top of their field

## Key Principles

**Visual Priority**
Ideas communicate through space, form, color, and composition — not paragraphs. Text is always minimal and visual-first. If you find yourself writing a sentence to explain something, replace it with a visual.

**Craftsmanship Standard**
The output must demonstrate master-level execution. Every detail requires meticulous attention. Spacing is flawless. Typography is intentional. Composition is considered from every angle. The work should appear worthy of museum or magazine display.

**Typography Approach**
- Font selection must be design-forward and serve the composition's conceptual framework
- Text remains sparse and integrated visually, not added decoratively
- Never use generic system fonts for a premium artifact

**Conceptual Depth**
Embed subtle, niche references that sophisticated viewers intuit without literal announcement — "like a jazz musician quoting another song." Avoid over-explaining the concept.

**Technical Requirements**
- All elements contain within canvas boundaries with proper margins
- Nothing falls off the page
- Nothing overlaps unintentionally
- Breathing room around every element

## Output Formats

- Design philosophy documentation (`.md` file) — optional, documents the aesthetic decisions
- Final visual artifact (`.pdf` or `.png`)
- Optional multi-page series for expanded concepts

## For IBR Reports

When generating the premium teaser report PDFs:
- The philosophy should reflect urgency + opportunity — "this business is invisible but rescuable"
- Color palette: authority (dark navy/slate) + urgency accent (amber/gold)
- Typography: use the existing ReportLab setup in `modules/full_audit_builder.py`
- Templates live in `templates/` — use Jinja2 for dynamic content insertion
- Output goes to `output/` via `modules/output_path_manager.py`

The goal is a report so visually compelling that the recipient feels the gap between where they are and where they could be — before they've read a single word.
