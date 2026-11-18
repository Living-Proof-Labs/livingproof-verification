# Run report: results first, state the outcome once, trials on demand

Mock for the published run report (reporter: `livingproof-capability-report`). Example data: Unitree G1 Walk the hallway (PASS) and Booster T1 Walk the hallway (FAIL).

Open `index.html` (pass), `fail.html`, `pass-trials.html`, `pass-about.html`, `fail-about.html`. Screens in `screens/` (`current-*` = today, `mock-*` = proposal; 390 and 1280, light, dark at 390).

## Order
1. Hero: robot and software, scenario title, ONE outcome badge, "3 of 3 repeats agree", one-line finding (FAIL only), Expected sentence.
2. Evaluators: Scenario tile selected (title once, version, purpose line, pass/fail); Run integrity beside it, quiet.
3. Steps: one row per step: criterion, result, observed vs limit. Identical repeats collapse to "Same in all 3 repeats". "Show trials" on demand; failing or disagreeing steps open themselves and the primary failure is first. Capped / not measured: one footnote icon per step.
4. About this run (collapsed): world, simulator, policy, spec, checkers, run id, limits, files, small print. Replaces the Summary table and Test context.

## Dropped
Summary table, Mission (= Expected), Why trials failed, repeated outcome, title, spec version and SHA, per-row caps caveat, caveat paragraphs under every table.

## Commit IDs
Never visible text. Each is a GitHub link; text is the tag if one resolves, else "commit". Full SHA only in href, title, aria-label. Covers spec provenance, robot pin, runner/verifier/reporter pins, and the bundle pin.

## Open questions
- Failing step with identical trials: "Same in all 3 repeats" and still auto-open (as mocked)?
- Failing steps first, or spec order?
- Mobile tiles are 9.75rem so two fit at 390.
- Keep Run integrity as a tile when it always passes?
- "CI run" link points at /actions/; conflicts with the no-/actions/ lint flagged in #129?
