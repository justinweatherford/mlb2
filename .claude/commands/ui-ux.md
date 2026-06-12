# UI/UX Pro Max — Design Intelligence

Comprehensive design guide for web and mobile applications. Contains 50+ styles, 161 color palettes, 57 font pairings, 99 UX guidelines, and 25 chart types. Use when making any UI structure, visual design, interaction pattern, or UX quality decision.

## Source

nextlevelbuilder/ui-ux-pro-max-skill

## When to Apply

### Must Use
- Designing new pages (Dashboard, Results Table, Score Cards, Report Preview)
- Creating or refactoring UI components (buttons, modals, forms, tables, score displays)
- Choosing color schemes, typography, spacing, or layout
- Reviewing UI code for UX, accessibility, or visual consistency
- Implementing navigation, animations, or responsive behavior

### Skip
- Pure backend logic
- API or database design
- Non-visual scripts or automation

**Decision criteria**: If the task changes how something **looks, feels, moves, or is interacted with**, use this skill.

## Rule Priority (Follow 1→10)

| Priority | Category | Impact | Key Rules |
|----------|----------|--------|-----------|
| 1 | Accessibility | CRITICAL | Contrast 4.5:1, Alt text, Keyboard nav |
| 2 | Touch & Interaction | CRITICAL | Min 44×44px, Loading feedback |
| 3 | Performance | HIGH | WebP/AVIF, Lazy loading, CLS < 0.1 |
| 4 | Style Selection | HIGH | Match product type, SVG icons (no emoji) |
| 5 | Layout & Responsive | HIGH | Mobile-first, No horizontal scroll |
| 6 | Typography & Color | MEDIUM | Base 16px, Line-height 1.5, Semantic tokens |
| 7 | Animation | MEDIUM | 150–300ms, transform/opacity only |
| 8 | Forms & Feedback | MEDIUM | Visible labels, Error near field |
| 9 | Navigation | HIGH | Predictable back, Deep linking |
| 10 | Charts & Data | LOW | Legends, Tooltips, Accessible colors |

## Workflow for New UI Work

### Step 1: Analyze Requirements

Extract from the request:
- **Product type**: Operator tool (professional, data-dense, dark-mode-first)
- **Audience**: Solo operator / agency user (not consumers)
- **Style keywords**: premium, dark, data-forward, professional

### Step 2: Check Existing Design System

This project has an established design system in `app.py`. Before designing anything new:

1. Read the existing CSS in `app.py` (lines ~21-200)
2. Read `modules/ui_components.py` for existing component patterns
3. Match the existing aesthetic — don't introduce competing styles

Existing tokens:
- Background: dark radial gradient (`#182033` → `#080B12` → `#05070B`)
- H1: `#ffffff`, 1.625rem, weight 800
- H2: `#f8fafc`, 1.625rem, weight 700
- Body text: `#94a3b8`
- Max content width: 1200px

### Step 3: Domain Searches (if search.py is available)

If the ui-ux-pro-max scripts are installed locally:
```bash
python3 skills/ui-ux-pro-max/scripts/search.py "<query>" --design-system
python3 skills/ui-ux-pro-max/scripts/search.py "<query>" --domain <domain>
```

Available domains: `product`, `style`, `typography`, `color`, `landing`, `chart`, `ux`, `react`

For this project, most relevant: `--domain ux`, `--domain chart` (for score displays), `--domain style "dark premium data"`

## Critical Rules for This Project

### Icons
- Use SVG icons only — no emojis as UI elements
- Consistent stroke width across all icons
- Touch target minimum 44×44px even for small icons

### Color
- Use semantic tokens, not raw hex in components
- Dark mode is the primary mode — design for it first
- Test contrast: primary text ≥4.5:1, secondary text ≥3:1 against dark backgrounds

### Animation
- Duration 150-300ms for micro-interactions
- Use `transform` and `opacity` only — never animate `width`, `height`, or layout properties
- Every animation must express cause-effect, not just be decorative
- Respect `prefers-reduced-motion`

### Data Display (Score Cards, Tables)
- Use tabular/monospaced figures for scores and numbers (prevents layout shift)
- Charts: always show legend, provide tooltips on hover
- Empty states: show meaningful message + action, never a blank container
- Loading: use skeleton/shimmer for any async operation >300ms

## Pre-Delivery Checklist

Before shipping any UI component:

- [ ] No emojis used as icons (use SVG instead)
- [ ] All tappable elements have pressed/hover feedback
- [ ] Primary text contrast ≥4.5:1
- [ ] Matches existing dark theme aesthetic
- [ ] Semantic color tokens used (no raw hex in new components)
- [ ] Loading and empty states handled
- [ ] No horizontal scroll introduced

## Common Anti-Patterns to Avoid in This Project

| Anti-Pattern | Why | Fix |
|---|---|---|
| Default Streamlit widget colors | Breaks the premium dark theme | Override with CSS injection |
| Emoji as status indicators | Inconsistent rendering | Use colored SVG dots or badges |
| Blocking spinner during scan | Feels slow | Show progress steps with ETA |
| Dense text tables | Hard to scan quickly | Use card layout with visual score indicators |
| Gray-on-gray text | Fails contrast check | Use `#94a3b8` minimum on dark backgrounds |
