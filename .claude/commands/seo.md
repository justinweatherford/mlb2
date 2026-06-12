# SEO: Universal SEO Analysis Skill

**Invocation:** `/seo <command> <url>`

Comprehensive SEO analysis across all industries. Orchestrates technical SEO, content quality, schema markup, local SEO, AI search optimization, and more. Especially relevant for analyzing the digital presence of veterinary clinics and other local businesses that are targets for Invisible Business Rescue.

## Quick Reference

| Command | What it does |
|---------|-------------|
| `/seo audit <url>` | Full website audit with parallel subagent delegation |
| `/seo page <url>` | Deep single-page analysis |
| `/seo local <url>` | Local SEO analysis (GBP, citations, reviews, map pack) — **most relevant for vet clinics** |
| `/seo technical <url>` | Technical SEO audit (9 categories) |
| `/seo content <url>` | E-E-A-T and content quality analysis |
| `/seo schema <url>` | Schema.org detection, validation, generation |
| `/seo sitemap <url>` | XML sitemap analysis or generation |
| `/seo images <url>` | Image SEO audit |
| `/seo geo <url>` | AI Overviews / Generative Engine Optimization |
| `/seo plan <business-type>` | Strategic SEO planning |
| `/seo backlinks <url>` | Backlink profile analysis |
| `/seo google [command] [url]` | Google APIs (GSC, PageSpeed, CrUX, GA4) |

## Orchestration Logic for Full Audit

When `/seo audit` is invoked:
1. Detect business type (local service, SaaS, e-commerce, publisher, agency)
2. Spawn parallel subagents: technical, content, schema, sitemap, performance, visual, geo
3. If local business detected → also spawn local SEO agent
4. Collect results and generate unified report with SEO Health Score (0-100)
5. Walk PERCEIVE → ANALYZE → VALIDATE → ACT synthesis before bucketing findings
6. Create prioritized action plan: Critical / High / Medium / Low
7. Offer PDF report generation

## Industry Detection

Detect business type from homepage signals:
- **Local Service**: phone number, address, service area, "serving [city]", Google Maps embed → auto-suggest `/seo local`
- **SaaS**: pricing page, /features, /integrations, /docs, "free trial"
- **E-commerce**: /products, /cart, "add to cart", product schema

For veterinary clinics (IBR primary vertical), always run `/seo local` after `/seo audit`.

## SEO Health Score Weights

| Category | Weight |
|----------|--------|
| Technical SEO | 22% |
| Content Quality | 23% |
| On-Page SEO | 20% |
| Schema / Structured Data | 10% |
| Performance (CWV) | 10% |
| AI Search Readiness | 10% |
| Images | 5% |

## Priority Levels

- **Critical**: Blocks indexing or causes penalties — fix immediately
- **High**: Significantly impacts rankings — fix within 1 week
- **Medium**: Optimization opportunity — fix within 1 month
- **Low**: Nice to have — backlog

## Synthesis Methodology

Full audits walk every phase before emitting the action plan:
- **PERCEIVE**: observe-external, observe-internal, listen
- **ANALYZE**: think, connect-lateral, connect-system
- **VALIDATE**: feel, accept
- **ACT**: create, grow

Each recommendation must carry:
- The first-principle observation it rests on
- Dependency/unblock relationship to other recommendations
- An explicit "how would we know this failed?" check
- A leading indicator the user can monitor without re-running the audit

## Quality Gates

- WARNING at 30+ location pages (enforce 60%+ unique content)
- HARD STOP at 50+ location pages (require user justification)
- Never recommend HowTo schema (deprecated Sept 2023)
- All Core Web Vitals references use INP, never FID

## Application to IBR

This skill is directly applicable to scanning target businesses:
- Run `/seo audit <vet_clinic_url>` to get the full digital presence picture
- Run `/seo local <vet_clinic_url>` for GBP, citations, reviews analysis
- Use the SEO Health Score (0-100) as input to `modules/opportunity_scorer.py`
- The Critical/High findings become the raw material for the teaser report narrative

## Error Handling

| Scenario | Action |
|----------|--------|
| URL unreachable | Report error, don't guess site content |
| Unrecognized command | List available commands, suggest closest match |
| Sub-skill fails during audit | Report partial results, note which sub-skill failed |
| Ambiguous business type | Present top two detected types, ask user to confirm |
