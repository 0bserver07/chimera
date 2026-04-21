"""Hub adapters: push/pull ``.chi`` bundles to/from remote stores.

A :class:`HubAdapter` is a tiny interface for moving a compiled bundle
between the local program registry and a remote backend.  Two built-in
backends are provided:

- :class:`HFHubAdapter` stores each bundle as a single file inside a
  Hugging Face Hub repository.  URIs look like
  ``hf://<org>/<repo>/<slug>.chi``.
- :class:`S3HubAdapter` stores bundles in an S3-compatible object store
  (AWS S3, Cloudflare R2, MinIO, ...).  URIs look like
  ``s3://<bucket>/<prefix>/<slug>.chi``.

Both adapters:

- Treat ``.chi`` archives as opaque bytes — no repacking or re-hashing.
- Raise :class:`ImportError` with an install hint when their optional
  dependency is missing.
- Redact any credentials surfaced in error messages (tokens, secret
  keys, endpoint query strings) so logs never leak them.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - type hints only
    pass


__all__ = [
    "HubAdapter",
    "HFHubAdapter",
    "HubError",
    "S3HubAdapter",
    "parse_hub_spec",
    "parse_hf_uri",
    "parse_s3_uri",
]


class HubError(RuntimeError):
    """Base class for hub adapter failures."""


# ---------------------------------------------------------------------------
# URI helpers
# ---------------------------------------------------------------------------


_HF_URI_RE = re.compile(r"^hf://(?P<repo>[^/]+/[^/]+)/(?P<path>.+)$")
_S3_URI_RE = re.compile(r"^s3://(?P<bucket>[^/]+)/(?P<key>.+)$")


def parse_hf_uri(uri: str) -> tuple[str, str]:
    """Split ``hf://<org>/<repo>/<path>`` into ``(repo_id, path)``.

    Args:
        uri: URI of the form ``hf://<org>/<repo>/<path>``.

    Returns:
        Tuple ``(repo_id, path_in_repo)`` where ``repo_id`` is ``"org/repo"``.

    Raises:
        ValueError: If ``uri`` does not match the ``hf://`` scheme.
    """
    match = _HF_URI_RE.match(uri)
    if not match:
        raise ValueError(
            f"expected 'hf://<org>/<repo>/<path>' URI, got {uri!r}"
        )
    return match.group("repo"), match.group("path")


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Split ``s3://<bucket>/<key>`` into ``(bucket, key)``.

    Args:
        uri: URI of the form ``s3://<bucket>/<key>``.

    Returns:
        Tuple ``(bucket, key)``.

    Raises:
        ValueError: If ``uri`` does not match the ``s3://`` scheme.
    """
    match = _S3_URI_RE.match(uri)
    if not match:
        raise ValueError(f"expected 's3://<bucket>/<key>' URI, got {uri!r}")
    return match.group("bucket"), match.group("key")


def parse_hub_spec(spec: str) -> HubAdapter:
    """Construct a :class:`HubAdapter` from a short CLI spec string.

    Supported forms:

    - ``"hf:<org>/<repo>"`` -> :class:`HFHubAdapter` (private by default)
    - ``"s3:<bucket>"`` -> :class:`S3HubAdapter` with default prefix
    - ``"s3:<bucket>/<prefix>"`` -> :class:`S3HubAdapter` with that prefix

    Args:
        spec: CLI-style adapter specifier, e.g. ``"hf:acme/functions"``.

    Returns:
        A ready-to-use :class:`HubAdapter`.

    Raises:
        ValueError: If ``spec`` cannot be parsed.
    """
    if spec.startswith("hf:"):
        repo_id = spec[len("hf:"):]
        if "/" not in repo_id:
            raise ValueError(
                f"hf spec must be 'hf:<org>/<repo>', got {spec!r}"
            )
        return HFHubAdapter(repo_id=repo_id)
    if spec.startswith("s3:"):
        remainder = spec[len("s3:"):]
        if "/" in remainder:
            bucket, prefix = remainder.split("/", 1)
        else:
            bucket, prefix = remainder, "chimera-fs"
        if not bucket:
            raise ValueError(f"s3 spec must be 's3:<bucket>[/<prefix>]', got {spec!r}")
        return S3HubAdapter(bucket=bucket, prefix=prefix)
    raise ValueError(
        f"unknown hub spec {spec!r}; expected 'hf:<org>/<repo>' or "
        "'s3:<bucket>[/<prefix>]'"
    )


# ---------------------------------------------------------------------------
# Credential redaction
# ---------------------------------------------------------------------------


_REDACT_RE = re.compile(
    r"(?i)(aws[_-]?secret[_-]?access[_-]?key|aws[_-]?access[_-]?key[_-]?id|"
    r"secret[_-]?key|access[_-]?key|token|authorization|api[_-]?key)"
    r"\s*[:=]\s*[^\s,;'\"]+"
)


def _redact(message: str) -> str:
    """Return ``message`` with obvious credential tokens blanked out."""
    return _REDACT_RE.sub(lambda m: f"{m.group(1)}=<redacted>", message)


# ---------------------------------------------------------------------------
# Base adapter
# ---------------------------------------------------------------------------


class HubAdapter(ABC):
    """Push/pull ``.chi`` bundles to/from a remote store."""

    @abstractmethod
    def push(self, slug: str, bundle_path: Path, *, description: str = "") -> str:
        """Upload a bundle file and return the remote URI.

        Args:
            slug: Local slug identifying the bundle (e.g. ``"classify-1a2b"``).
            bundle_path: Path to the ``.chi`` archive on disk.
            description: Optional free-form description to store alongside
                the bundle (when the backend supports it).

        Returns:
            A URI string that can later be passed to :meth:`pull`.

        Raises:
            HubError: On backend failures (network, auth, etc.).
        """

    @abstractmethod
    def pull(self, uri: str) -> bytes:
        """Download a bundle by URI and return its raw bytes.

        Args:
            uri: A URI previously produced by :meth:`push` (or by
                :meth:`list`).

        Returns:
            The bytes of the ``.chi`` archive, unchanged.

        Raises:
            HubError: On backend failures.
            ValueError: If ``uri`` does not match this adapter's scheme.
        """

    @abstractmethod
    def list(self) -> list[dict[str, str]]:
        """Enumerate remote bundles.

        Returns:
            A list of ``{"slug": ..., "uri": ..., "description": ...}``
            dicts, one per remote bundle.
        """


# ---------------------------------------------------------------------------
# Hugging Face Hub backend
# ---------------------------------------------------------------------------


_HF_HUB_INSTALL_HINT = (
    "HFHubAdapter requires huggingface_hub. "
    "Install with: pip install 'chimera-run[function_synthesis]'"
)


class HFHubAdapter(HubAdapter):
    """Hugging Face Hub backend for ``.chi`` bundles.

    Each bundle is stored as a single file at ``<slug>.chi`` inside a
    Hugging Face repository.  URIs returned by :meth:`push` have the form
    ``hf://<org>/<repo>/<slug>.chi``.

    Args:
        repo_id: Target repository, formatted as ``"org/repo"``.
        token: Optional Hugging Face token.  When ``None``, the underlying
            ``huggingface_hub`` client falls back to its usual lookup
            (env var ``HF_TOKEN``, cached login).
        private: Whether to create the repo as private if it does not yet
            exist.  Defaults to ``True``.
    """

    def __init__(
        self,
        *,
        repo_id: str,
        token: str | None = None,
        private: bool = True,
    ) -> None:
        if "/" not in repo_id:
            raise ValueError(
                f"repo_id must be 'org/repo', got {repo_id!r}"
            )
        try:
            from huggingface_hub import HfApi  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised via tests
            raise ImportError(_HF_HUB_INSTALL_HINT) from exc
        self.repo_id = repo_id
        self._token = token
        self.private = private
        self._api: Any = HfApi(token=token)

    # ------------------------------------------------------------------ push
    def push(self, slug: str, bundle_path: Path, *, description: str = "") -> str:
        bundle_path = Path(bundle_path)
        if not bundle_path.exists():
            raise FileNotFoundError(f"bundle not found: {bundle_path}")
        path_in_repo = f"{slug}.chi"
        try:
            # Ensure the repo exists.  ``exist_ok=True`` makes this a no-op
            # when the repo is already there.
            self._api.create_repo(
                repo_id=self.repo_id,
                private=self.private,
                exist_ok=True,
            )
            commit_message = f"chimera-fs push: {slug}"
            if description:
                commit_message = f"{commit_message} — {description}"
            self._api.upload_file(
                path_or_fileobj=str(bundle_path),
                path_in_repo=path_in_repo,
                repo_id=self.repo_id,
                commit_message=commit_message,
            )
        except Exception as exc:  # noqa: BLE001 - normalize + redact
            raise HubError(
                f"HFHubAdapter.push failed: {_redact(str(exc))}"
            ) from None
        return f"hf://{self.repo_id}/{path_in_repo}"

    # ------------------------------------------------------------------ pull
    def pull(self, uri: str) -> bytes:
        repo_id, path_in_repo = parse_hf_uri(uri)
        try:
            from huggingface_hub import hf_hub_download  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - import guarded at init
            raise ImportError(_HF_HUB_INSTALL_HINT) from exc
        try:
            local_path = hf_hub_download(
                repo_id=repo_id,
                filename=path_in_repo,
                token=self._token,
            )
        except Exception as exc:  # noqa: BLE001
            raise HubError(
                f"HFHubAdapter.pull failed: {_redact(str(exc))}"
            ) from None
        return Path(local_path).read_bytes()

    # ------------------------------------------------------------------ list
    def list(self) -> list[dict[str, str]]:
        try:
            files = list(
                self._api.list_repo_files(repo_id=self.repo_id)
            )
        except Exception as exc:  # noqa: BLE001
            raise HubError(
                f"HFHubAdapter.list failed: {_redact(str(exc))}"
            ) from None
        out: list[dict[str, str]] = []
        for name in files:
            if not name.endswith(".chi"):
                continue
            slug = name[: -len(".chi")]
            out.append(
                {
                    "slug": slug,
                    "uri": f"hf://{self.repo_id}/{name}",
                    "description": "",
                }
            )
        return out


# ---------------------------------------------------------------------------
# S3-compatible backend
# ---------------------------------------------------------------------------


_S3_INSTALL_HINT = (
    "S3HubAdapter requires boto3. "
    "Install with: pip install 'chimera-run[function_synthesis_s3]'"
)


class S3HubAdapter(HubAdapter):
    """S3-compatible object-storage backend (AWS S3, R2, MinIO).

    Bundles are stored as objects at ``<prefix>/<slug>.chi``.  URIs
    returned by :meth:`push` have the form
    ``s3://<bucket>/<prefix>/<slug>.chi``.

    Args:
        bucket: Target bucket name.
        prefix: Key prefix applied to every uploaded bundle.  Defaults to
            ``"chimera-fs"``.  Leading/trailing slashes are stripped.
        endpoint_url: Optional endpoint URL for non-AWS backends such as
            Cloudflare R2 or MinIO.  When ``None``, the default AWS
            endpoint is used.
    """

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "chimera-fs",
        endpoint_url: str | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("bucket must be non-empty")
        try:
            import boto3  # type: ignore[import-not-found, import-untyped, unused-ignore]
        except ImportError as exc:  # pragma: no cover - exercised via tests
            raise ImportError(_S3_INSTALL_HINT) from exc
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.endpoint_url = endpoint_url
        client_kwargs: dict[str, Any] = {}
        if endpoint_url is not None:
            client_kwargs["endpoint_url"] = endpoint_url
        self._client: Any = boto3.client("s3", **client_kwargs)

    def _key_for(self, slug: str) -> str:
        if self.prefix:
            return f"{self.prefix}/{slug}.chi"
        return f"{slug}.chi"

    # ------------------------------------------------------------------ push
    def push(self, slug: str, bundle_path: Path, *, description: str = "") -> str:
        bundle_path = Path(bundle_path)
        if not bundle_path.exists():
            raise FileNotFoundError(f"bundle not found: {bundle_path}")
        key = self._key_for(slug)
        extra: dict[str, Any] = {}
        if description:
            extra["Metadata"] = {"description": description}
        try:
            self._client.upload_file(
                Filename=str(bundle_path),
                Bucket=self.bucket,
                Key=key,
                ExtraArgs=extra or None,
            )
        except Exception as exc:  # noqa: BLE001
            raise HubError(
                f"S3HubAdapter.push failed: {_redact(str(exc))}"
            ) from None
        return f"s3://{self.bucket}/{key}"

    # ------------------------------------------------------------------ pull
    def pull(self, uri: str) -> bytes:
        bucket, key = parse_s3_uri(uri)
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            body = response["Body"]
            if hasattr(body, "read"):
                data = body.read()
            else:
                data = bytes(body)
        except Exception as exc:  # noqa: BLE001
            raise HubError(
                f"S3HubAdapter.pull failed: {_redact(str(exc))}"
            ) from None
        if not isinstance(data, bytes):
            data = bytes(data)
        return data

    # ------------------------------------------------------------------ list
    def list(self) -> list[dict[str, str]]:
        kwargs: dict[str, Any] = {"Bucket": self.bucket}
        if self.prefix:
            kwargs["Prefix"] = f"{self.prefix}/"
        try:
            response = self._client.list_objects_v2(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise HubError(
                f"S3HubAdapter.list failed: {_redact(str(exc))}"
            ) from None
        out: list[dict[str, str]] = []
        for obj in response.get("Contents", []) or []:
            key = obj.get("Key", "")
            if not key.endswith(".chi"):
                continue
            name = key.rsplit("/", 1)[-1]
            slug = name[: -len(".chi")]
            out.append(
                {
                    "slug": slug,
                    "uri": f"s3://{self.bucket}/{key}",
                    "description": "",
                }
            )
        return out
