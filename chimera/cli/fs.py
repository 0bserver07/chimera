"""`chimera fs` subcommands: compile, run, list, rm, info, import-peft, login, rename."""
from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
from pathlib import Path
from typing import Any

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.compiler import CompilerBackend
from chimera.function_synthesis.compilers.mock import MockCompiler
from chimera.function_synthesis.compilers.remote import RemoteCompiler
from chimera.function_synthesis.convert import import_peft, save_peft_bundle
from chimera.function_synthesis.credentials import CredentialStore
from chimera.function_synthesis.errors import CacheMissError
from chimera.function_synthesis.hub import HubAdapter, parse_hub_spec
from chimera.function_synthesis.registry import ProgramRegistry, slug_for
from chimera.function_synthesis.spec import FunctionSpec


_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _valid_slug(slug: str) -> bool:
    """Return ``True`` if ``slug`` is alphanumeric + ``.-_`` and non-empty."""
    return bool(_SLUG_RE.match(slug))


def _load_spec(path: Path) -> FunctionSpec:
    text = path.read_text()
    data = json.loads(text)
    return FunctionSpec(
        name=data["name"],
        description=data["description"],
        examples=data.get("examples", []),
        input_schema=data.get("input_schema"),
        output_schema=data.get("output_schema"),
    )


def _build_compiler(name: str, *, endpoint: str | None, api_key: str | None) -> CompilerBackend:
    if name == "mock":
        return MockCompiler()
    if name == "remote":
        if not endpoint:
            raise SystemExit("--endpoint required with --compiler remote")
        return RemoteCompiler(endpoint=endpoint, api_key=api_key)
    raise SystemExit(f"unknown --compiler {name!r}; expected 'mock' or 'remote'")


def cmd_compile(args: argparse.Namespace) -> int:
    spec = _load_spec(Path(args.spec))
    compiler = _build_compiler(args.compiler, endpoint=args.endpoint, api_key=args.api_key)
    bundle = compiler.compile(spec)
    registry = ProgramRegistry.default()
    slug = registry.install(spec=spec, bundle=bundle)
    print(slug)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    registry = ProgramRegistry.default()
    entry = registry.resolve(args.slug)
    from chimera.function_synthesis.backends.llama_cpp import LlamaCppBackend
    from chimera.function_synthesis.runtime import CompiledFunction

    backend = LlamaCppBackend(base_model_path=args.base_model)
    with CompiledFunction.from_path(entry.bundle_path, backend=backend) as fn:
        print(fn(args.input, max_tokens=args.max_tokens))
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    registry = ProgramRegistry.default()
    for entry in registry.list():
        print(f"{entry.slug}\t{entry.spec.name}\t{entry.spec.description}")
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    ProgramRegistry.default().remove(args.slug)
    return 0


def cmd_import_peft(args: argparse.Namespace) -> int:
    spec = _load_spec(Path(args.spec))
    peft_dir = Path(args.peft_dir)
    prompts_path = Path(args.prompts) if args.prompts else None
    prompts: dict[str, Any]
    if prompts_path and prompts_path.exists():
        prompts = json.loads(prompts_path.read_text())
    else:
        prompts = {"system": spec.description, "user_template": "{input}", "stop": []}

    bundle = import_peft(
        peft_dir,
        spec=spec,
        prompts=prompts,
        base_model=args.base_model,
    )

    slug = args.out or slug_for(spec)
    registry = ProgramRegistry.default()
    # Write the PEFT .chi directly into the registry's bundles directory so
    # the peft/ subtree is preserved (ChiBundle.save would strip it).
    target = registry.dirs.bundles / f"{slug}.chi"
    target.parent.mkdir(parents=True, exist_ok=True)
    save_peft_bundle(bundle, peft_dir, target)

    # Update the index manually — ProgramRegistry has no hook for
    # pre-written archives, but the format of each index entry matches.
    index_file = registry.dirs.index_file
    index = json.loads(index_file.read_text()) if index_file.exists() else {}
    index[slug] = {
        "bundle_path": str(target),
        "spec": json.loads(spec.to_json()),
        "metadata": bundle.metadata,
    }
    index_file.write_text(json.dumps(index, sort_keys=True, indent=2))

    # Sanity-check that what we wrote still loads as a ChiBundle.
    ChiBundle.load(target)

    print(slug)
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    """Upload an installed bundle to a remote hub."""
    registry = ProgramRegistry.default()
    entry = registry.resolve(args.slug)
    adapter = parse_hub_spec(args.hub)
    description = args.description or entry.spec.description
    uri = adapter.push(args.slug, entry.bundle_path, description=description)
    print(uri)
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    """Download a bundle from a remote hub and install it locally."""
    uri = args.uri
    adapter: HubAdapter
    if uri.startswith("hf://"):
        # Reuse the same repo_id embedded in the URI for the pull client.
        from chimera.function_synthesis.hub import HFHubAdapter, parse_hf_uri

        repo_id, _ = parse_hf_uri(uri)
        adapter = HFHubAdapter(repo_id=repo_id)
    elif uri.startswith("s3://"):
        from chimera.function_synthesis.hub import S3HubAdapter, parse_s3_uri

        bucket, _ = parse_s3_uri(uri)
        adapter = S3HubAdapter(bucket=bucket, prefix="")
    else:
        raise SystemExit(
            f"unknown URI scheme: {uri!r}; expected 'hf://' or 's3://'"
        )

    data = adapter.pull(uri)

    # Parse the bundle (to recover the spec) from the raw bytes, then write
    # the exact downloaded bytes into the registry.  Round-tripping through
    # ``ChiBundle.save`` would rewrite the manifest timestamp and lose byte
    # equality, so we bypass ``registry.install`` here.
    import tempfile

    registry = ProgramRegistry.default()
    registry.dirs.ensure()
    with tempfile.NamedTemporaryFile(suffix=".chi", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        bundle = ChiBundle.load(tmp_path)
        slug = args.slug or slug_for(bundle.spec)
        target = registry.dirs.bundles / f"{slug}.chi"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        index_file = registry.dirs.index_file
        index = (
            json.loads(index_file.read_text()) if index_file.exists() else {}
        )
        index[slug] = {
            "bundle_path": str(target),
            "spec": json.loads(bundle.spec.to_json()),
            "metadata": bundle.metadata,
        }
        index_file.write_text(json.dumps(index, sort_keys=True, indent=2))
    finally:
        tmp_path.unlink(missing_ok=True)

    print(slug)
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    entry = ProgramRegistry.default().resolve(args.slug)
    payload = {
        "slug": entry.slug,
        "bundle_path": str(entry.bundle_path),
        "spec": json.loads(entry.spec.to_json()),
        "metadata": entry.metadata,
    }
    print(json.dumps(payload, indent=2))
    return 0


def _fail(message: str, code: int = 1) -> int:
    """Print ``message`` to stderr and return ``code``.

    Also raises :class:`SystemExit` with the same code so that
    ``python -m chimera`` exits non-zero even when the caller does not
    forward the return value.  Tests that invoke handlers in-process can
    still catch :class:`SystemExit` or rely on the returned code.
    """
    print(message, file=sys.stderr)
    raise SystemExit(code)


def cmd_login(args: argparse.Namespace) -> int:
    """Manage credentials for remote function-synthesis services.

    Modes:
        * ``--list``: print stored service names (no tokens).
        * ``<service> --delete``: remove credentials for ``service``.
        * ``<service> [--token T]``: save ``T`` (or a token read from stdin
          via ``getpass`` so it is not echoed) for ``service``.
    """
    store = CredentialStore()

    if args.list:
        for name in store.list_services():
            print(name)
        return 0

    service = args.service
    if not service:
        _fail("error: <service> is required (or use --list)", code=2)

    if args.delete:
        store.delete(service)
        print(f"Removed credentials for {service}")
        return 0

    token = args.token
    if token is None:
        # getpass hides the input so the token never echoes to the terminal.
        try:
            token = getpass.getpass(f"Token for {service}: ")
        except (EOFError, KeyboardInterrupt):
            _fail("\nerror: no token provided", code=1)

    if not token:
        # Never include the (empty) token in the message; keep phrasing generic.
        _fail("error: empty token", code=1)

    try:
        store.set(service, token)
    except ValueError:
        # Do not echo the token in error paths.
        _fail("error: invalid service or token", code=1)

    print(f"Saved credentials for {service}")
    return 0


def cmd_rename(args: argparse.Namespace) -> int:
    """Rename an installed program slug.

    The registry validates existence and collisions; this handler adds a
    format check on ``new_slug`` (alphanumeric plus ``.-_``) so invalid
    filenames never reach disk.
    """
    new_slug = args.new_slug
    if not _valid_slug(new_slug):
        _fail(
            f"error: invalid slug {new_slug!r}; "
            "use alphanumerics, '-', '_' or '.'",
            code=2,
        )

    registry = ProgramRegistry.default()
    try:
        registry.rename(args.old_slug, new_slug)
    except CacheMissError:
        _fail(f"error: slug not found: {args.old_slug}", code=1)
    except ValueError as exc:
        _fail(f"error: {exc}", code=1)

    print(new_slug)
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    fs = subparsers.add_parser("fs", help="function-synthesis operations")
    fs_sub = fs.add_subparsers(dest="fs_cmd", required=True)

    p_compile = fs_sub.add_parser("compile", help="compile a FunctionSpec into a .chi bundle")
    p_compile.add_argument("spec", help="path to a spec JSON file")
    p_compile.add_argument("--compiler", default="mock", choices=["mock", "remote"])
    p_compile.add_argument("--endpoint", default=None)
    p_compile.add_argument("--api-key", default=None)
    p_compile.set_defaults(func=cmd_compile)

    p_run = fs_sub.add_parser("run", help="invoke an installed program")
    p_run.add_argument("slug")
    p_run.add_argument("input")
    p_run.add_argument("--base-model", required=True, help="path to base GGUF")
    p_run.add_argument("--max-tokens", type=int, default=256)
    p_run.set_defaults(func=cmd_run)

    p_list = fs_sub.add_parser("list", help="list installed programs")
    p_list.set_defaults(func=cmd_list)

    p_rm = fs_sub.add_parser("rm", help="remove an installed program")
    p_rm.add_argument("slug")
    p_rm.set_defaults(func=cmd_rm)

    p_info = fs_sub.add_parser("info", help="show details for a slug")
    p_info.add_argument("slug")
    p_info.set_defaults(func=cmd_info)

    p_push = fs_sub.add_parser(
        "push",
        help="upload an installed bundle to a remote hub",
    )
    p_push.add_argument("slug", help="slug of the installed bundle to push")
    p_push.add_argument(
        "--hub",
        required=True,
        help=(
            "remote backend, e.g. 'hf:<org>/<repo>' or 's3:<bucket>[/<prefix>]'"
        ),
    )
    p_push.add_argument(
        "--description",
        default=None,
        help="optional human-readable description for the remote entry",
    )
    p_push.set_defaults(func=cmd_push)

    p_pull = fs_sub.add_parser(
        "pull",
        help="download a bundle by URI and install it into the local registry",
    )
    p_pull.add_argument(
        "uri",
        help="remote URI, e.g. 'hf://<org>/<repo>/<slug>.chi' or 's3://<bucket>/<key>'",
    )
    p_pull.add_argument(
        "--slug",
        default=None,
        help="override the installed slug (default: slug_for(spec))",
    )
    p_pull.set_defaults(func=cmd_pull)

    p_import = fs_sub.add_parser(
        "import-peft",
        help="package a HuggingFace PEFT adapter directory as a .chi bundle",
    )
    p_import.add_argument("peft_dir", help="path to a PEFT adapter directory")
    p_import.add_argument("spec", help="path to a spec JSON file")
    p_import.add_argument(
        "--prompts",
        default=None,
        help="optional JSON file with {system, user_template, stop}",
    )
    p_import.add_argument(
        "--base-model",
        default=None,
        help="override base model identifier (defaults to value in adapter_config.json)",
    )
    p_import.add_argument(
        "--out",
        default=None,
        help="override the installed slug (default: <name>-<hash8>)",
    )
    p_import.set_defaults(func=cmd_import_peft)

    p_login = fs_sub.add_parser(
        "login",
        help="save, list, or delete credentials for a remote service",
    )
    p_login.add_argument(
        "service",
        nargs="?",
        default=None,
        help="service name (e.g. 'huggingface', 's3', 'compile.example.com')",
    )
    p_login.add_argument(
        "--token",
        default=None,
        help="token value; if omitted, read from stdin via getpass",
    )
    p_login.add_argument(
        "--list",
        action="store_true",
        help="list stored service names (no tokens printed)",
    )
    p_login.add_argument(
        "--delete",
        action="store_true",
        help="delete credentials for <service>",
    )
    p_login.set_defaults(func=cmd_login)

    p_rename = fs_sub.add_parser(
        "rename",
        help="rename an installed program slug",
    )
    p_rename.add_argument("old_slug", help="current slug")
    p_rename.add_argument("new_slug", help="new slug (alphanumerics, '-', '_', '.')")
    p_rename.set_defaults(func=cmd_rename)
