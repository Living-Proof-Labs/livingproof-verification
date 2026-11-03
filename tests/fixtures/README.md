# Test fixtures

`reporter-v3.0.3-go1-report.html` is a real `report.html` written by
livingproof-capability-report v3.0.3 in CI run 41703287431 (verify on 8df8e90), artifact
`unitree-go1-playground-mujoco-hallway-capability-report-playground`.

- Original: 292535 bytes, sha256 `ee656f7cba835ddc5396f687c341bc43c221ec1945bb774153e4f9312cd83aed`
  (as listed in that artifact's `SHA256SUMS`).
- Fixture: the same bytes with only the `<details><summary>Full lossless report</summary>…</details>`
  block (the 285 kB lossless JSON) removed: 7308 bytes, sha256
  `cd6149a0b882d5c6b0749c880b3745750a365b2aa86aaaae9c33df1b8982d88f`.

Its Evidence section carries the reporter's `CI run` anchor and three Actions artifact anchors,
which is what `tools/site.py` must rewrite to text (lpv #23, Vera's review of PR #27).
