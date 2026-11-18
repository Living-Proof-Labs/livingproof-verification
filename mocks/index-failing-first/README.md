# Index: failing results first, robots expand to their scenarios

Design mock for the verification index after [PR #135](https://github.com/Living-Proof-Labs/livingproof-verification/pull/135). It prepares the page for many more robots and scenarios, and it brings failures to the top of attention. “Being negative is how we get things fixed.”

This is a **separate issue from [#138](https://github.com/Living-Proof-Labs/livingproof-verification/issues/138)**. The mock assumes #138’s card behaviour throughout: the whole card opens the latest report, the “Latest report” link is gone, and **History (n)** is the only explicit link (stretched-link pattern, History sits above the overlay, 44px tall on mobile).

The data is an **example catalog**, not the live one: 6 robots and 68 scenarios (8–15 each), with 3 failures across different robots. Apptronik Apollo, Fourier GR1, and Unitree H1 are illustrative.

## Layout

### 1. Needs attention (top)

Every scenario whose latest result is FAIL, across all robots, **newest first**.

Each card is the #138 card, plus:

- The robot and configuration on the subtitle, e.g. `Booster T1 · stock · MuJoCo`
- Failing evaluator chips, using the exact stored title (`Scenario`, not “Scenario Evaluator”). One chip per human reviewer (e.g. `Aaron Roller`).
- An optional one-line finding.

When nothing fails, this section collapses to one quiet line: **All N latest results pass.**

### 2. Robot sections (below)

Native `<details>` / `<summary>`, **collapsed by default**.

The summary shows:

- Robot name
- Scenario count, with the fail count in the fail colour and the pass count quiet (`12 scenarios · 1 failed · 11 passed`)
- A strip of one dot per scenario (teal pass, red fail) so you can see what’s inside at a glance
- The latest version and time (from the most recently run scenario)

Dot order matches the expanded list: failing first, then alphabetical, so reds cluster at the start of the strip.

Expanded: the scenario cards, failing first, then alphabetical. Subtitles stay `stock · MuJoCo` here, because the robot is already in the header. A failing scenario appears both in Needs attention and again inside its robot, so the section is complete.

Each robot has an anchor (`#booster-t1`, `#unitree-g1`, …) that **opens that section when linked**. A small script sets `details.open` from the hash; native `<details>` does not open on `:target` alone.

### 3. Mobile (390)

The summary wraps cleanly:

```
▸  Unitree G1
   15 scenarios · 1 failed · 14 passed
   ● ● ● ● ● ● ● ● ● +6     v1.1.0 · 20 minutes ago
```

The dot strip stays on one line. When the dots do not fit beside the version and time, they truncate and a **+n** count shows the rest. (Implementation note: `[hidden]` must win over `.pip { display: block }`, or the hidden dots still take space.)

## Accessibility

- Native disclosure widget: Enter / Space toggles; state is announced as expanded / collapsed.
- The summary’s accessible name is a visually hidden sentence that includes the counts, e.g. “Unitree G1, 15 scenarios, 1 failed, 14 passed, latest v1.1.0, 20 minutes ago.” The decorative dots are `aria-hidden`.
- Cards follow #138: the title link’s accessible name is “Walk the hallway, latest report (FAIL).” Tab order is the card link, then History, then any evaluator chips.
- `:focus-visible` uses the teal ring (on the summary, and on the whole card when its report link is focused).
- History is at least 44px tall at 390.

## Pages

- `index.html` — default: failures at the top, robots collapsed
- `expanded.html` — the same catalog, with Unitree G1 open
- `all-pass.html` — the same catalog with every latest result passing
- `styles.css`, `open.js`

## Screenshots

- `screens/collapsed-desktop.png` (1280)
- `screens/collapsed-mobile.png` (390)
- `screens/expanded-desktop.png` (1280)
- `screens/expanded-mobile.png` (390)
- `screens/all-pass-desktop.png` (1280)

## Open questions

1. **Robot order.** The mock sorts robots alphabetically, which scales better than a fixed lab order. A curated catalog order is an alternative.
2. **Dot order.** Fail-first (matching the expanded list) makes problems obvious. A stable catalog order would make the strip a map of “which scenario,” at the cost of scattering the reds.
3. **Deep links.** `#unitree-g1` opens that robot. It does not scroll to a specific scenario inside it. If we need `#unitree-g1/walk-the-hallway`, say so.

## ASCII sketch

```
Living Proof Labs
Verification reports
The latest result for each robot and scenario. Failures come first, so they get fixed.

NEEDS ATTENTION
3 latest results failed, newest first.

┌──────────────────────────────────────────────────────────────────┐
│ FAIL  Climb the stairs                                           │
│       Fourier GR1 · stock · Isaac Sim                            │
│       Scenario  Aaron Roller                                     │
│       Missed the first step and did not recover upright.         │
│       v1.1.0 · 10 minutes ago                        History (16)│
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│ FAIL  Walk the hallway                                           │
│       Unitree G1 · stock · MuJoCo                                │
│       Scenario                                                   │
│       Drifted 0.66 m from the centreline, then touched the wall. │
│       v1.1.0 · 30 minutes ago                        History (87)│
└──────────────────────────────────────────────────────────────────┘

ROBOTS
▸ Apptronik Apollo  10 scenarios · 10 passed     ●●●●●●●●●●     v1.1.0 · 20 minutes ago
▸ Booster T1        12 scenarios · 1 failed · 11 passed  ●●●●…  v1.1.0 · 20 minutes ago
▸ Fourier GR1       14 scenarios · 1 failed · 13 passed  ●●●●…  v1.1.0 · 10 minutes ago
▾ Unitree G1        15 scenarios · 1 failed · 14 passed  ●●●●…  v1.1.0 · 20 minutes ago
    FAIL  Walk the hallway   stock · MuJoCo   Scenario   History (87)
    PASS  Climb the stairs   stock · MuJoCo              History (8)
    PASS  Cross the doorway  stock · MuJoCo              History (7)
    …alphabetical, then the remaining robots, still collapsed
```

When nothing fails:

```
All 68 latest results pass.

ROBOTS
▸ Apptronik Apollo  10 scenarios · 10 passed     ●●●●●●●●●●     v1.1.0 · 20 minutes ago
▸ …
```
