# Listing pipeline v3: plan and decisions

Status: Phase 0 drafted 2026-10-18, awaiting user review. Nothing built yet.

## Why
The first multi-agent runs (BK-261014-002 Wodehouse, BK-261015-001 Persian/Japanese,
BK-261015-002 Poe) missed on collector condition, description and pricing. Validators checked
form, not content; prompts were soft; pricing had no sold data; condition prompt lacked grading
definitions and told agents to omit minor toning/soiling (contrary to trade practice).

## Decisions (user-confirmed)
- **Track is chosen by the user**, not inferred: `--track collector|modern` at ingest. Age is only a
  sanity warning.
- **Formal trade grading**, no age adjustment, no "conservative" fudge: As New, Fine, Near Fine,
  Very Good, Good, Fair, Poor; +/- allowed; jacket graded separately; ex-library always stated;
  every defect disclosed. "This is eBay, not Christie's": plain, honest, not florid.
  See `docs/grading-rubric.md`.
- **Template = Psmith in the City** (item 177412641967). Blurb, then a light facts list, then one
  prose condition paragraph with a closing verdict line. No per-category grading tables.
  See `docs/template-psmith.md`.
- **eBay hosts photos after publish** (i.ebayimg.com); R2 is only needed until publish.

## Architecture
- Python driver owns the loop: steps, validation, up to 3 retries, stops at signoff. Each role
  runs headless via `claude -p` or `codex exec`; model per role in config.
- Each step = planner (mostly code/templates) -> generator (role) -> evaluator (different model,
  ideally other vendor; checks evidence and photos, returns specific fixes). Code validators run
  first.
- Per-step folders `data/runs/SKU/<step>/`: plan.json, brief.md, output.json, eval.json,
  corrections. Inputs read-only; one writable output.
- Steps: images -> edition -> condition -> (pricing || description) -> assemble -> signoff.
- Condition: hidden structured fields (jacket, boards, spine, hinges, text block, plates, marks,
  repairs), each with a term and photo ID. Code renders the Psmith-style paragraph.
- Description: model writes only the 2-4 sentence blurb; code assembles the rest.
- Pricing: `docs/sources.toml` registry per track + a source scout that builds an author dossier
  (collector track). Terapeak via the user's logged-in browser (not headless).
- `/edit/SKU` page in `lister serve`: HTML editor + live preview; human edits are locked against
  reruns. Interim: VS Code Live Preview on `data/runs/SKU/description.html`.

## Phases
0. Standards: rubric, template, sources.toml. **(drafted, needs review)**
1. Foundation: track flag, driver, per-step folders, config for models. **(built 2026-10-18;
   `lister drive SKU`, `lister track SKU`, `ingest --track`, `[orchestrator]` in config.toml;
   evaluator hook runs `lister/roles/<role>.eval.md` when present; none written yet)**
2. Condition + description on the new rubric/template; re-run the 3 test SKUs; user grades.
3. Pricing: logged-in browser, source scout, evaluator.
4. Image prep step and `/edit` page.
5. Edition and modern-pricing evaluators; full end-to-end test.

## Open inputs from the user
- Browser for Terapeak: user's personal-Gmail Chrome profile (logged into eBay), via Claude in Chrome.
- Codex: installed and logged in (ChatGPT); default evaluator engine.
- Author-specific resources to seed (e.g. Wodehouse: McIlvaine bibliography, Wodehouse societies).
