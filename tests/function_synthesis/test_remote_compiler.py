from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock

import pytest

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.compiler import CompilerError
from chimera.function_synthesis.compilers.remote import RemoteCompiler
from chimera.function_synthesis.spec import FunctionSpec


def _zip_bytes() -> bytes:
    spec = FunctionSpec(name="echo", description="echo")
    bundle = ChiBundle(
        spec=spec,
        adapter_bytes=b"ADAPTER",
        prompts={"system": "", "user_template": "{input}", "stop": []},
        metadata={"compiler_backend": "remote"},
    )
    buf = io.BytesIO()
    # mirror ChiBundle.save but write to a buffer
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "x.chi"
        bundle.save(path)
        return path.read_bytes()


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"", text: str = "") -> None:
        self.status_code = status_code
        self.content = content
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")


def test_remote_compiler_posts_spec_and_returns_bundle(monkeypatch):
    payload = _zip_bytes()
    fake_post = MagicMock(return_value=_FakeResponse(200, content=payload))

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, json, headers, timeout):
            return fake_post(url, json=json, headers=headers, timeout=timeout)

    import chimera.function_synthesis.compilers.remote as mod
    monkeypatch.setattr(mod, "_Client", _FakeClient)

    compiler = RemoteCompiler(endpoint="https://example.test/compile", api_key="secret")
    bundle = compiler.compile(FunctionSpec(name="echo", description="echo"))

    assert bundle.spec.name == "echo"
    assert bundle.metadata["compiler_backend"] == "remote"
    fake_post.assert_called_once()
    _, kwargs = fake_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer secret"
    assert kwargs["json"]["spec"]["name"] == "echo"


def test_remote_compiler_raises_compiler_error_on_http_failure(monkeypatch):
    class _FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, json, headers, timeout):
            return _FakeResponse(500, text="boom")

    import chimera.function_synthesis.compilers.remote as mod
    monkeypatch.setattr(mod, "_Client", _FakeClient)

    compiler = RemoteCompiler(endpoint="https://example.test/compile")
    with pytest.raises(CompilerError, match="500"):
        compiler.compile(FunctionSpec(name="x", description="y"))


def test_remote_compiler_requires_httpx_when_missing(monkeypatch):
    import chimera.function_synthesis.compilers.remote as mod
    monkeypatch.setattr(mod, "_Client", None)
    compiler = RemoteCompiler(endpoint="https://example.test/compile")
    with pytest.raises(ImportError, match="httpx"):
        compiler.compile(FunctionSpec(name="x", description="y"))
