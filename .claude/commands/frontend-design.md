# Frontend Design

Generate distinctive, production-grade frontend interfaces that avoid generic AI aesthetics.

## Source

Anthropic Claude Code Plugin — `anthropics/claude-code/blob/main/plugins/frontend-design/README.md`
Authors: Prithvi Rajasekaran, Alexander Bricken (Anthropic)

## What It Does

Claude automatically applies this skill for frontend work. Creates production-ready code with:

- **Bold aesthetic choices** — make a clear directional decision, don't hedge
- **Distinctive typography and color palettes** — not defaults, not Bootstrap
- **High-impact animations and visual details** — purposeful motion that enhances UX
- **Context-aware implementation** — the aesthetic matches the product's purpose

## Usage

Describe the interface you want and Claude will choose a clear aesthetic direction and implement production code with meticulous attention to detail.

**Examples:**
```
"Create a business scan results dashboard"
"Build the opportunity score card component"
"Design the discovery results table with dark theme"
"Add a loading state for the scan progress"
```

## For This Project (Streamlit)

Since this app uses Streamlit, frontend design is implemented via:
1. **CSS injection** via `st.markdown("""<style>...</style>""", unsafe_allow_html=True)` — see `app.py` lines 21-200 for the existing design system
2. **Custom HTML components** via `st.markdown(html, unsafe_allow_html=True)`
3. **`modules/ui_components.py`** — reusable Streamlit component helpers

### Existing Design System in `app.py`

The app already has a premium dark theme established:
- Background: `radial-gradient(circle at top left, #182033 0%, #080B12 34%, #05070B 100%)`
- Primary text: `#ffffff`
- Secondary text: `#94a3b8`
- Accent: use existing color tokens, don't introduce new ones without reason

**Always match the existing aesthetic.** New components should feel like they belong in the same product.

### Anti-Patterns to Avoid

- Default Streamlit widget styling without CSS override
- Generic color schemes (blues and grays from a color picker)
- No hover states or transitions
- Dense text-heavy layouts — this is a premium operator tool, it should breathe
- Generic loading spinners — the existing app uses custom progress indicators

## Quality Bar

The output should look like a senior product designer built it. Before shipping any UI component, ask: "Would someone screenshot this to show off?" If the answer is no, keep refining.
