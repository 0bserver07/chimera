from __future__ import annotations

from chimera.core.loop_deps import LoopDeps, production_deps


def test_loop_deps_has_required_fields() -> None:
    deps = LoopDeps(
        call_model=lambda *a, **kw: None,
        compact=lambda *a, **kw: None,
    )
    assert callable(deps.call_model)
    assert callable(deps.compact)
    assert callable(deps.uuid)
    # uuid default produces a non-empty string
    result = deps.uuid()
    assert isinstance(result, str)
    assert len(result) > 0
