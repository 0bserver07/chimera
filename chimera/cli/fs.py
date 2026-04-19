"""`chimera fs` subcommands: compile, run, list, rm, info, import-peft."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.compiler import CompilerBackend
from chimera.function_synthesis.compilers.mock import MockCompiler
from chimera.function_synthesis.compilers.remote import RemoteCompiler
from chimera.function_synthesis.convert import import_peft, save_peft_bundle
from chimera.function_synthesis.registry import ProgramRegistry, slug_for
from chimera.function_synthesis.spec import FunctionSpec


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
    prompts: dict
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
