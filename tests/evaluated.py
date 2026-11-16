# SPDX-License-Identifier: Apache-2.0
"""A minimal source bundle whose trials carry an evaluator result (issue #46).

``tools/site.py`` refuses to publish a report whose assessed trials lack one, so
test reports that are meant to publish include this bundle.
"""

DESCRIPTOR = {
    "title": "Scenario",
    "short_title": "Scenario",
    "purpose": "Checks each step of the scenario against the limits in its spec.",
    "version": "1.2.1",
    "tier": "verdict",
    "findings": [],
}


def evaluator_result() -> dict:
    return {
        "format": "livingproof-evaluator-result/1.1.0",
        "evaluator": {"id": "livingproof-scenario"},
        "descriptor": dict(DESCRIPTOR),
    }


def bundle(trials: int = 1) -> dict:
    return {
        "results": [
            {"id": f"trial-{i:02d}", "evaluator_results": [evaluator_result()]}
            for i in range(1, trials + 1)
        ]
    }
