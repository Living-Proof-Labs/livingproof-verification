from html import escape as e
from pathlib import Path
root = Path(__file__).parent

GH = "https://github.com/Living-Proof-Labs"

def pin(repo, sha, tag=None, what=None):
    """A commit/pin as a GitHub link. Visible text is the tag, else the word 'commit'.
    The full SHA lives only in href and title/aria-label, never as visible text."""
    text = tag or "commit"
    label = f"{what or repo}, {tag or 'commit'} (commit {sha})"
    cls = "pin" + ("" if tag else " is-fallback")
    return (f'<a class="{cls}" href="{GH}/{repo}/commit/{sha}" title="{e(label)}" '
            f'aria-label="{e(label)}">{e(text)}</a>')

CHECK = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.2 8.2 6.1 11.1 12.8 4.4" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
CROSS = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>'
SCENARIO = '<svg class="glyph" viewBox="0 0 24 24" fill="none" aria-hidden="true"><g stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 7L10.5 11L17.5 4"/><rect x="4" y="9" width="16" height="11" rx="4"/></g><g fill="currentColor"><circle cx="8.5" cy="14.5" r="1.5"/><circle cx="15.5" cy="14.5" r="1.5"/></g></svg>'
INTEGRITY = '<svg class="glyph" viewBox="0 0 24 24" fill="none" aria-hidden="true"><g stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5" width="16" height="14" rx="3"/><path d="M7.5 13l2.2-3 2.6 4 1.8-2.5 2.4 1.5"/></g></svg>'

EXPECTED = ("The robot starts at the hallway entrance and is commanded to walk straight ahead at 0.5 m/s. "
            "It must reach the exit, 5.5 m along the route, within 27 s, staying upright, never touching either wall, "
            "and staying within its lateral limit of the centreline.")
CAPPED = "Capped at 0.25 m, not a real measurement."

def S(code, crit, ok, obs, time_kind, rows, note=None, finding=None, primary=False, same=True):
    return dict(code=code, crit=crit, ok=ok, obs=obs, time_kind=time_kind, rows=rows, note=note,
                finding=finding, primary=primary, same=same)

PASS = dict(
    key="pass",
    robot_title="Unitree G1", software="playground 1.0.1", scenario="Walk the hallway", outcome="PASS",
    speaks=None,
    steps=[
        S("hallway-exit-reached", "Reaches the exit, 5.5 m along the route, within 27 s, crossing it inside the exit segment.", True,
          "<b>9.60 s</b> of 27 s allowed", "Exit time", [("9.60 s of 27 s allowed", "9.60 s")]),
        S("hallway-wall-contact", "Never touches a side wall.", True, "<b>No contacts</b>", "No contact", [("0 contacts", "—")], note=CAPPED),
        S("hallway-fall", "Stays upright: the base never drops below the fall height.", True,
          "Lowest <b>0.7065 m</b>, needs at least 0.5 m", "Lowest height", [("0.7065 m (limit 0.5 m)", "2.90 s")]),
        S("hallway-lateral-limit", "Stays within its lateral limit of the hallway centreline.", True,
          "Furthest <b>0.0903 m</b> of 0.49 m allowed", "Largest offset", [("0.0903 m (limit 0.49 m)", "5.24 s")]),
    ],
    run_id="walk-hallway-unitree-g1-playground-20261118T223044Z", ci_url=f"{GH}/livingproof-verification/actions/runs/44411940359",
    pins=dict(
        robot=("livingproof-unitree-g1", "3c8a781dc42b56126cc599e00a7d7ed360ed6671", "v2.2.11"),
        spec=("robot-capability-specs", "57317e617014a6df97bb7fba9d92a890af8d42e5", "v2.3.0"),
        verifier=("capability-verifier", "e6ff270170a70c7a0707a4c53de43c6ab96604b5", "v2.5.0"),
        reporter=("livingproof-capability-report", "bc1a05eaeb5c27fd084cd29d2cd399bb485310e6", "v3.11.6"),
        runner=("livingproof-sim-mujoco", "4e97b6c1ee72a9f6598a91d0a1476116dc17ae13", "v3.4.1"),
        bundle=("livingproof-verification", "d9919bedec5265d42406f7fcae3696153bf0c91a", "v1.2.2")),
    limits=[("Time limit", "27 s: twice the route time at 0.5 m/s, plus 5 s."),
            ("Lateral limit", "0.49 m: half the 1.5 m hallway minus half the 0.52 m robot width."),
            ("Fall height", "0.5 m: the lowest the base may go.")],
    evidence=[("Video, trial 1", "#"), ("MCAP, all 3 trials", "#")],
    repo_note="")

FAIL = dict(
    key="fail",
    robot_title="Booster T1", software="stock 1.0.0", scenario="Walk the hallway", outcome="FAIL",
    speaks="Primary failure: drifted 0.66 m from the centreline at 7.26 s, then touched the right wall at 7.80 s.",
    steps=[
        S("hallway-lateral-limit", "Stays within its lateral limit of the hallway centreline.", False,
          "<b>0.6594 m</b> at 7.26 s, 0.4795 m allowed", "First exceeded", [("0.6594 m (limit 0.4795 m)", "7.26 s")],
          finding="Drifted 0.66 m from the centreline at 7.26 s.", primary=True),
        S("hallway-wall-contact", "Never touches a side wall.", False,
          "<b>1 contact</b>, 0.1525 m deep at 7.80 s, none allowed", "First contact", [("1 contact (limit 0): right wall, 0.1525 m deep", "7.80 s")],
          finding="Touched the right wall at 7.80 s."),
        S("hallway-exit-reached", "Reaches the exit, 5.5 m along the route, within 27 s, crossing it inside the exit segment.", False,
          "<b>Did not exit</b>; 27 s allowed", "First missed", [("Not reached (limit 27 s)", "8.56 s")],
          finding="Left the hallway through the right wall at 8.56 s."),
        S("hallway-fall", "Stays upright: the base never drops below the fall height.", True,
          "Lowest <b>0.6407 m</b>, needs at least 0.44 m", "Lowest height", [("0.6407 m (limit 0.44 m)", "0.68 s")]),
    ],
    run_id="walk-hallway-booster-t1-stock-20261118T223051Z", ci_url=f"{GH}/livingproof-verification/actions/runs/44411940359",
    pins=dict(
        # Example: this robot pin has no tag, to show the 'commit' fallback.
        robot=("livingproof-booster-t1", "a00c7d884b764c89404dc91be9232c0d55eb392c", None),
        spec=("robot-capability-specs", "57317e617014a6df97bb7fba9d92a890af8d42e5", "v2.3.0"),
        verifier=("capability-verifier", "e6ff270170a70c7a0707a4c53de43c6ab96604b5", "v2.5.0"),
        reporter=("livingproof-capability-report", "bc1a05eaeb5c27fd084cd29d2cd399bb485310e6", "v3.11.6"),
        runner=("livingproof-sim-mujoco", "4e97b6c1ee72a9f6598a91d0a1476116dc17ae13", "v3.4.1"),
        bundle=("livingproof-verification", "d9919bedec5265d42406f7fcae3696153bf0c91a", "v1.2.2")),
    limits=[("Time limit", "27 s: twice the route time at 0.5 m/s, plus 5 s."),
            ("Lateral limit", "0.4795 m: half the 1.5 m hallway minus half the 0.541 m robot width."),
            ("Fall height", "0.44 m: the lowest the base may go.")],
    evidence=[("Video, trial 1", "#"), ("MCAP, all 3 trials", "#")],
    repo_note="Example: the robot pin has no tag, so it reads “commit”.")

def step_html(s, open_all=False):
    fail = not s["ok"]
    opened = " open" if (fail or open_all) else ""
    n = 3
    icon = f'<span class="s-icon {"s-fail" if fail else "s-pass"}" role="img" aria-label="{"Not met" if fail else "Met"}">{CROSS if fail else CHECK}</span>'
    note_icon = (f'<span class="note" tabindex="0" role="img" aria-label="{e(s["note"])}" title="{e(s["note"])}">i</span>'
                 if s["note"] else "")
    primary = '<p class="step-note">Primary failure</p>' if s["primary"] else ""
    same = f'Same in all {n} repeats' if s["same"] else "Repeats disagree"
    rows = "".join(
        f'<tr><td>{i}</td><td class="{"res-fail" if fail else "res-pass"}">{"Not met" if fail else "Met"}</td><td>{e(r[0])}</td><td>{e(s["time_kind"])} {e(r[1])}</td></tr>'
        for i in range(1, n + 1) for r in s["rows"])
    foot = (f'<p class="footnote"><span class="note" aria-hidden="true">i</span>{e(s["note"])}</p>' if s["note"] else "")
    return f'''<details class="step{" is-fail" if fail else ""}"{opened}>
  <summary>
    {icon}
    <div class="s-main"><p class="crit">{e(s["crit"])}</p><p class="obs">{s["obs"]}{note_icon}</p>{primary}</div>
    <div class="s-side"><span>{same}</span><br><span class="more"><span class="t-show">Show trials</span><span class="t-hide">Hide trials</span></span></div>
  </summary>
  <div class="trials">{foot}
    <table class="t"><thead><tr><th scope="col">Trial</th><th scope="col">Result</th><th scope="col">Observed vs limit</th><th scope="col">Time</th></tr></thead><tbody>{rows}</tbody></table>
    <p class="code">{e(s["code"])} · v1.0.0</p>
  </div>
</details>'''

def page(d, *, open_all=False, open_about=False, title_suffix=""):
    fail = d["outcome"] == "FAIL"
    p = d["pins"]
    P = lambda k, what=None: pin(p[k][0], p[k][1], p[k][2], what or p[k][0])
    steps = "\n".join(step_html(s, open_all) for s in d["steps"])
    n_fail = sum(1 for s in d["steps"] if not s["ok"])
    sc_cls = "eval-mark is-speaking" if fail else "eval-mark"
    sc_pip = f'<span class="pip is-fail" aria-hidden="true">{CROSS}</span>' if fail else f'<span class="pip" aria-hidden="true">{CHECK}</span>'
    sc_state = "not met" if fail else "satisfied"
    ok_note = "" if fail else '<span class="ok">All satisfied</span>'
    speaks = f'<p class="speaks">{e(d["speaks"])}</p>' if d["speaks"] else ""
    ev = "".join(f'<a href="{u}">{e(t)}</a>' for t, u in d["evidence"])
    limits = "".join(f"<dt>{e(a)}</dt><dd>{e(b)}</dd>" for a, b in d["limits"])
    about_open = " open" if open_about else ""
    note = f'<p class="smallprint">{e(d["repo_note"])}</p>' if d["repo_note"] else ""
    return f'''<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Run report, decluttered ({d["key"]}){title_suffix} · example mock</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,650;0,9..40,700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="styles.css">
<body>
<header class="masthead"><div class="wrap masthead-inner">
  <div><p class="eyebrow">Living Proof Labs</p><p class="crumb"><a href="#">← Run history</a></p></div>
  <fieldset class="theme"><legend>Colour theme</legend>
    <label><input type="radio" name="theme" id="theme-system" value="system" checked> System</label>
    <label><input type="radio" name="theme" id="theme-light" value="light"> Light</label>
    <label><input type="radio" name="theme" id="theme-dark" value="dark"> Dark</label></fieldset>
</div></header>
<main class="page wrap">
  <p class="banner">Example mock for review, not the live report. Content follows the {e(d["robot_title"])} {e(d["scenario"])} run. Commit IDs are never visible text: each is a GitHub link, labelled with its tag or the word “commit”.</p>

  <section class="card hero" aria-labelledby="h1">
    <p class="kicker">{e(d["robot_title"])} · {e(d["software"])}</p>
    <h1 id="h1">{e(d["scenario"])}</h1>
    <div class="hero-outcome"><span class="badge badge-{"fail" if fail else "pass"}">{d["outcome"]}</span><span class="agree">3 of 3 repeats agree</span></div>
    {speaks}
    <p class="expected"><b>Expected</b> {e(EXPECTED)}</p>
  </section>

  <section class="card" aria-labelledby="evals">
    <p class="evals-label" id="evals">Evaluators{ok_note}</p>
    <div class="eval-row" role="group" aria-label="Evaluators">
      <button type="button" class="{sc_cls}" id="tab-scenario" aria-pressed="true" aria-controls="panel-scenario" data-panel="panel-scenario">
        <span class="eval-face" role="img" aria-label="Scenario, {sc_state}">{SCENARIO}{sc_pip}</span><span class="eval-name">Scenario</span></button>
      <button type="button" class="eval-mark" id="tab-integrity" aria-pressed="false" aria-controls="panel-integrity" data-panel="panel-integrity">
        <span class="eval-face" role="img" aria-label="Run integrity, satisfied">{INTEGRITY}<span class="pip" aria-hidden="true">{CHECK}</span></span><span class="eval-name">Run integrity</span></button>
    </div>
    <div class="eval-panel" id="panel-scenario" role="region" aria-labelledby="tab-scenario">
      <p class="eval-meta"><span class="ver">1.3.0</span>Checks each step of the scenario against the limits in its spec.</p>
      <div class="steps">
{steps}
      </div>
      <p class="evidence">Evidence kept for 7 days · {ev}</p>
    </div>
    <div class="eval-panel" id="panel-integrity" role="region" aria-labelledby="tab-integrity" hidden>
      <p class="eval-meta"><span class="ver">1.0.0</span>Judges whether the run and the test system can be trusted.</p>
      <p class="obs"><b>Every planned trial has its observation file.</b></p>
    </div>
  </section>

  <details class="card about"{about_open}>
    <summary><span class="ttl">About this run</span><span class="sub">hallway-open · MuJoCo · spec {e(p["spec"][2] or "commit")}</span></summary>
    <div class="about-body">
      <dl class="kv">
        <dt>Scenario</dt><dd>walk-hallway</dd>
        <dt>Robot software</dt><dd>{e(d["software"])} · {P("robot", p["robot"][0])}</dd>
        <dt>World</dt><dd>hallway-open@0.1.0. A straight, empty hallway, 1.50 m wide and 6.00 m long, with 2.50 m-high walls.</dd>
        <dt>Simulator</dt><dd>MuJoCo · runner {P("runner")}</dd>
        <dt>Policy</dt><dd>all-required-v1@1.0.0</dd>
        <dt>Spec</dt><dd>{P("spec")} · walk-hallway · revision 0.1.0</dd>
        <dt>Checked by</dt><dd>capability-verifier {P("verifier")} · report layout {P("reporter")}</dd>
        <dt>Published in</dt><dd>livingproof-verification {P("bundle")} · <a href="{d["ci_url"]}">CI run</a></dd>
        <dt>Run</dt><dd class="mono">{e(d["run_id"])}</dd>
        {limits}
        <dt>Files</dt><dd><a href="#">report.json</a> · <a href="#">report.md</a></dd>
      </dl>
      <p class="smallprint">Recorded at run time; not reassessed. Time is when a step first failed, the exit time on success, or the time of the extreme value.</p>
      {note}
    </div>
  </details>
  <p class="footer">Fixture evidence, not robot safety certification.</p>
</main>
<script>
document.querySelectorAll(".eval-mark").forEach(function (b) {{
  b.addEventListener("click", function () {{
    document.querySelectorAll(".eval-mark").forEach(function (o) {{
      var on = o === b; o.setAttribute("aria-pressed", on);
      document.getElementById(o.dataset.panel).hidden = !on;
    }});
  }});
}});
</script>
</body>
</html>
'''

(root / "index.html").write_text(page(PASS))
(root / "pass-trials.html").write_text(page(PASS, open_all=True, title_suffix=" trials shown"))
(root / "pass-about.html").write_text(page(PASS, open_about=True, title_suffix=" about open"))
(root / "fail.html").write_text(page(FAIL))
(root / "fail-about.html").write_text(page(FAIL, open_about=True, title_suffix=" about open"))
print("built")
