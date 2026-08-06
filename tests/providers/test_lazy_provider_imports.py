"""Resolving one provider must not import the other nine.

``create_provider`` called ``_ensure_builtins_registered()``, which imported all
ten built-in provider modules — and the ``anthropic`` and ``openai`` vendor SDKs
with them — in order to construct **one**. Measured cost of that eager import on
a modest Linux box: **1942 ms warm**, against **566 ms** for the one module
actually needed. It was ~99% of ``chimera code``'s time-to-first-prompt.

Two things are pinned here, because the fix has two distinct ways to rot:

1. **Behaviour** — asking for Anthropic must not load the OpenAI SDK. Checked in
   a subprocess, since ``sys.modules`` in this one is already polluted by the
   test suite itself and would make the assertion vacuous.
2. **The table** — ``_BUILTIN_MODULES`` is a hand-written map from provider name
   to owning module, and a hand-written map drifts. It is re-derived here by
   importing each module in a clean interpreter and reading ``list_providers()``.
   That matters: three modules (``modal_endpoint``, ``xai``, ``acmecloud``) also
   register ``compatible`` as a side effect, which reading the source misses.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from chimera.providers.registry import _BUILTIN_MODULES

#: Modules that register a name they do not own, as a side effect of importing
#: another provider module. Derived, not assumed — see the module docstring.
_TRANSITIVE = {"compatible"}


def _in_subprocess(code: str) -> str:
    """Run *code* in a clean interpreter and return its stdout, asserting success."""
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=180
    )
    assert proc.returncode == 0, f"subprocess failed:\n{proc.stderr[-2000:]}"
    return proc.stdout.strip()


class TestOnlyTheNeededProviderIsImported:
    def test_resolving_anthropic_does_not_import_the_openai_sdk(self) -> None:
        """The headline guarantee, and the one users feel as startup latency."""
        out = _in_subprocess(
            "import sys, json\n"
            "from chimera.providers.factory import create_provider\n"
            "try:\n"
            "    create_provider(model='claude-sonnet-4-20250514', api_key='x')\n"
            "except Exception:\n"
            "    pass\n"
            "print(json.dumps({\n"
            "  'openai': 'openai' in sys.modules,\n"
            "  'ollama': 'chimera.providers.ollama' in sys.modules,\n"
            "  'google': 'chimera.providers.google' in sys.modules,\n"
            "  'anthropic_mod': 'chimera.providers.anthropic' in sys.modules,\n"
            "}))"
        )
        seen = json.loads(out.splitlines()[-1])
        assert seen["anthropic_mod"], "the module we asked for must be loaded"
        assert not seen["openai"], "the OpenAI SDK must not load to talk to Anthropic"
        assert not seen["ollama"], "unrelated provider module loaded"
        assert not seen["google"], "unrelated provider module loaded"

    def test_unknown_name_is_a_no_op_not_an_error(self) -> None:
        """A plugin may register a provider no built-in module owns."""
        out = _in_subprocess(
            "from chimera.providers.registry import get_provider_factory\n"
            "print(get_provider_factory('not-a-real-provider') is None)"
        )
        assert out.splitlines()[-1] == "True"

    def test_explicit_enumeration_still_loads_everything(self) -> None:
        """`list_providers` must stay complete — the error path depends on it."""
        out = _in_subprocess(
            "import json\n"
            "from chimera.providers.registry import ("
            "  _ensure_builtins_registered, list_providers)\n"
            "_ensure_builtins_registered()\n"
            "print(json.dumps(sorted(list_providers())))"
        )
        names = json.loads(out.splitlines()[-1])
        assert set(_BUILTIN_MODULES) <= set(names), (
            "eager registration must still register every mapped name"
        )


class TestTheNameToModuleTableMatchesReality:
    @pytest.mark.parametrize("module", sorted(set(_BUILTIN_MODULES.values())))
    def test_each_module_registers_the_names_mapped_to_it(self, module: str) -> None:
        """Re-derives the table rather than trusting it.

        A hand-written map is a claim about another module's import side
        effects — exactly the kind of claim that rots silently when someone
        adds or renames a provider.
        """
        out = _in_subprocess(
            f"import json, {module}\n"
            "from chimera.providers.registry import list_providers\n"
            "print(json.dumps(sorted(list_providers())))"
        )
        registered = set(json.loads(out.splitlines()[-1]))
        mapped = {n for n, m in _BUILTIN_MODULES.items() if m == module}

        missing = mapped - registered
        assert not missing, (
            f"{module} is mapped to {sorted(missing)} but does not register "
            "them — get_provider_factory would silently return None"
        )
        # Names it registers beyond its own are transitive imports; anything
        # else means the table has an unmapped name that lazy lookup misses.
        extra = registered - mapped - _TRANSITIVE
        assert not extra, (
            f"{module} also registers {sorted(extra)}, which is not in "
            "_BUILTIN_MODULES — add it, or lazy resolution will fail for it"
        )

    def test_every_registered_name_is_reachable_lazily(self) -> None:
        """The end-to-end invariant: enumerate eagerly, then resolve each name
        from a *cold* interpreter with no eager registration at all."""
        names = json.loads(
            _in_subprocess(
                "import json\n"
                "from chimera.providers.registry import ("
                "  _ensure_builtins_registered, list_providers)\n"
                "_ensure_builtins_registered()\n"
                "print(json.dumps(sorted(list_providers())))"
            ).splitlines()[-1]
        )
        unreachable = json.loads(
            _in_subprocess(
                "import json\n"
                "from chimera.providers.registry import get_provider_factory\n"
                f"names = {names!r}\n"
                "print(json.dumps("
                "  [n for n in names if get_provider_factory(n) is None]))"
            ).splitlines()[-1]
        )
        assert not unreachable, (
            f"registered but NOT lazily resolvable: {unreachable}. Every name "
            "list_providers() reports must be reachable without eager import."
        )
