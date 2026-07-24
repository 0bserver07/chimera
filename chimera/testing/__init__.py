"""Public testing utilities: hermetic agent-loop harness over the faux provider.

Import surface for test suites (Chimera's own and downstream users testing
their assembled agents)::

    from chimera.testing import create_harness

    run = create_harness(turns=[{"text": "done"}], workspace=tmp_path).run("go")
    assert run.reason == "completed"

The harness runs scripted turns through the **real** agent loop — no mocks of
the loop, no network. It complements (never replaces) the repo rule that a
feature is not "done" until verified against a real LLM.
"""
from chimera.providers.faux import FauxProvider, FauxProviderError
from chimera.testing.harness import (
    AgentHarness,
    DriverHarness,
    HarnessRun,
    create_assembled_harness,
    create_harness,
    default_test_tools,
)

__all__ = [
    "AgentHarness",
    "DriverHarness",
    "FauxProvider",
    "FauxProviderError",
    "HarnessRun",
    "create_assembled_harness",
    "create_harness",
    "default_test_tools",
]
