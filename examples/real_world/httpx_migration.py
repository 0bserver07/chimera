#!/usr/bin/env python3
"""Multi-file migration workflow: transition sync requests to async httpx.

This real-world example demonstrates using Chimera's MigrationPlanner and MigrationRule
to scan, plan, and execute a multi-file migration. The workflow converts a synchronous
legacy API client (`api_client.py`) and its calling coordinator (`service.py`) to
asynchronous logic utilizing `httpx.AsyncClient`.

The migration is verified using two automated gates:
- Gate A (Compile Gate): Verify the transformed code has no syntax errors using compile().
- Gate B (Execution/Logic Gate): Execute the async code using asyncio and AsyncMock to verify HTTP calls.

Usage:
    # Requires httpx for the verification gate: `uv sync --extra dev` (or `pip install httpx`)
    python examples/real_world/httpx_migration.py
"""

from __future__ import annotations

import asyncio
import difflib
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure chimera is in the Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from chimera.migration import MigrationPlanner, MigrationRule

# Legacy (pre-migration) source files
LEGACY_API_CLIENT = '''\
import requests

def get_user(user_id):
    """Fetch user by ID."""
    url = f"https://api.example.com/users/{user_id}"
    response = requests.get(url, timeout=5)
    return response.json()

def post_data(endpoint, payload):
    """Post structured data."""
    url = f"https://api.example.com/{endpoint}"
    response = requests.post(url, json=payload, timeout=10)
    return response.json()
'''

LEGACY_SERVICE = '''\
import api_client

def sync_user_profile(user_id):
    """Retrieve user and format profile."""
    user = api_client.get_user(user_id)
    payload = {"user_id": user_id, "status": "active"}
    result = api_client.post_data("profile/update", payload)
    return {"user": user, "update_result": result}
'''


def print_color_diff(old_content: str, new_content: str, filename: str) -> None:
    """Print a terminal-colorized unified diff of the changes."""
    diff = difflib.unified_diff(
        old_content.splitlines(),
        new_content.splitlines(),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm=""
    )
    for line in diff:
        if line.startswith("+") and not line.startswith("+++"):
            print(f"\033[32m{line}\033[0m")  # Green for added lines
        elif line.startswith("-") and not line.startswith("---"):
            print(f"\033[31m{line}\033[0m")  # Red for removed lines
        elif line.startswith("@@"):
            print(f"\033[36m{line}\033[0m")  # Cyan for diff sections
        elif line.startswith("---") or line.startswith("+++"):
            print(f"\033[1m{line}\033[0m")   # Bold for file headers
        else:
            print(line)


def main() -> None:
    print("=" * 80)
    print(" CHIMERA CODEBASE MIGRATION WORKFLOW: REQUESTS TO HTTPX ".center(80, "="))
    print("=" * 80)
    print()

    # Step 1: Setup Sandboxed Workspace
    print("[1/6] Creating sandboxed workspace directory...")
    with tempfile.TemporaryDirectory(prefix="chimera-migration-") as tmpdir:
        api_client_path = os.path.join(tmpdir, "api_client.py")
        service_path = os.path.join(tmpdir, "service.py")

        with open(api_client_path, "w", encoding="utf-8") as f:
            f.write(LEGACY_API_CLIENT)
        with open(service_path, "w", encoding="utf-8") as f:
            f.write(LEGACY_SERVICE)

        print(f"Created sandbox workspace at: {tmpdir}")
        print("  - api_client.py (Legacy Client using synchronous requests)")
        print("  - service.py    (Legacy Caller using cascaded synchronous APIs)")
        print()

        # Step 2: Configure custom MigrationPlanner & Rules
        print("[2/6] Configuring custom MigrationPlanner and rules...")
        planner = MigrationPlanner()

        # Import Translation Rule
        planner.add_rule(MigrationRule(
            pattern=r"\bimport requests\b",
            replacement="import httpx",
            description="Replace requests import with httpx",
            file_glob="*.py"
        ))

        # Async client functions definition rules
        planner.add_rule(MigrationRule(
            pattern=r"def get_user\(",
            replacement="async def get_user(",
            description="Convert def get_user to async def get_user",
            file_glob="api_client.py"
        ))
        planner.add_rule(MigrationRule(
            pattern=r"def post_data\(",
            replacement="async def post_data(",
            description="Convert def post_data to async def post_data",
            file_glob="api_client.py"
        ))

        # GET/POST Requests AsyncClient rules
        planner.add_rule(MigrationRule(
            pattern=r"(?m)^([ \t]*)response = requests\.get\(([^)]+)\)",
            replacement=r"\1async with httpx.AsyncClient() as client:\n\1    response = await client.get(\2)",
            description="Convert requests.get call to await client.get under AsyncClient",
            file_glob="api_client.py"
        ))
        planner.add_rule(MigrationRule(
            pattern=r"(?m)^([ \t]*)response = requests\.post\(([^)]+)\)",
            replacement=r"\1async with httpx.AsyncClient() as client:\n\1    response = await client.post(\2)",
            description="Convert requests.post call to await client.post under AsyncClient",
            file_glob="api_client.py"
        ))

        # Service calling function async converter
        planner.add_rule(MigrationRule(
            pattern=r"def sync_user_profile\(",
            replacement="async def sync_user_profile(",
            description="Convert sync_user_profile calling coordinator to async def",
            file_glob="service.py"
        ))

        # Await cascaded client APIs in service
        planner.add_rule(MigrationRule(
            pattern=r"api_client\.get_user\(",
            replacement="await api_client.get_user(",
            description="Add await keyword to api_client.get_user calls",
            file_glob="service.py"
        ))
        planner.add_rule(MigrationRule(
            pattern=r"api_client\.post_data\(",
            replacement="await api_client.post_data(",
            description="Add await keyword to api_client.post_data calls",
            file_glob="service.py"
        ))

        # Load files mapping
        files = {
            "api_client.py": LEGACY_API_CLIENT,
            "service.py": LEGACY_SERVICE,
        }

        # Step 3: Scan, Plan, and Preview
        print("\n[3/6] Scanning codebase and generating migration plan...")
        scan_results = planner.scan(files)
        
        print("\n--- Scan Match Logs ---")
        for path, matches in scan_results.items():
            print(f"\033[1m{path}\033[0m: {len(matches)} potential transformation(s)")
            for match in matches:
                print(f"  - {match}")

        migration_plan = planner.plan(files)
        print(f"\nGenerated Migration Plan: '{migration_plan.name}' ({migration_plan.description})")
        print(f"Total Rules Applicable: {len(migration_plan.rules)}")
        print()

        # Step 4: Apply and Persist transforms
        print("[4/6] Applying migration rules and writing back to files...")
        migrated_files = planner.apply(files)

        # Print Preview Diffs
        print("\n--- Migration Preview Diffs ---")
        for filename in files:
            print(f"\n\033[1;34mDiff for {filename}:\033[0m")
            print_color_diff(files[filename], migrated_files[filename], filename)

        # Write to actual temporary workspace files
        for filename, content in migrated_files.items():
            full_path = os.path.join(tmpdir, filename)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

        print("\nSuccessfully persisted migrated codebase back to sandbox workspace.")
        print()

        # Step 5: Automated Verification Gates
        print("[5/6] Starting verification gates...")

        # --- Gate A: Syntax Compile Check ---
        print("\n  -- Gate A: Syntax Compile Gate --")
        for path, content in migrated_files.items():
            try:
                compile(content, path, "exec")
                print(f"  \033[32m✔\033[0m Compiled {path} successfully (0 syntax errors).")
            except SyntaxError as e:
                print(f"  \033[31m✘\033[0m Compile error in {path} at line {e.lineno}: {e.text}")
                print("Aborting. Verification failed.")
                sys.exit(1)

        # --- Gate B: Integration / Mock Logic Check ---
        print("\n  -- Gate B: Logic Verification Gate --")
        
        # Inject the tempdir to the front of sys.path to allow dynamic imports
        sys.path.insert(0, tmpdir)

        try:
            # Dynamically import the newly refactored service module
            import api_client  # type: ignore[import-not-found]  # noqa: F401 — load tmpdir copy before service
            import service as migrated_service  # type: ignore[import-not-found]

            # Define asynchronous testing function
            async def run_logic_verification() -> None:
                # 1. Setup mock AsyncClient instance and response flows
                mock_client = AsyncMock()

                # Mock response for client.get
                mock_get_response = AsyncMock()
                mock_get_response.json = MagicMock(return_value={"id": 155, "username": "yad_developer"})
                mock_client.get.return_value = mock_get_response

                # Mock response for client.post
                mock_post_response = AsyncMock()
                mock_post_response.json = MagicMock(return_value={"success": True, "transaction_id": "tx_999"})
                mock_client.post.return_value = mock_post_response

                # Mock the AsyncClient async context manager to yield our mock client
                mock_context_manager = AsyncMock()
                mock_context_manager.__aenter__.return_value = mock_client

                print("  Executing migrated sync_user_profile with httpx patch...")
                with patch("httpx.AsyncClient", return_value=mock_context_manager):
                    result = await migrated_service.sync_user_profile(155)

                print("  Verification result returned payload:")
                print(f"    {result}")

                # Assert result structure matches our mocks
                assert result["user"]["username"] == "yad_developer", "User profile payload verification failed."
                assert result["update_result"]["success"] is True, "Service update payload verification failed."

                # Assert that the mocked client was called with correct parameters
                mock_client.get.assert_called_once_with("https://api.example.com/users/155", timeout=5)
                mock_client.post.assert_called_once_with(
                    "https://api.example.com/profile/update",
                    json={"user_id": 155, "status": "active"},
                    timeout=10
                )
                print("  \033[32m✔\033[0m Assertions passed: correct URL calls, headers, and parameters.")

            # Run async verification
            asyncio.run(run_logic_verification())
            print("  \033[32m✔\033[0m Gate B: Logical verification succeeded!")

        except Exception as e:
            import traceback
            print(f"  \033[31m✘\033[0m Verification execution failed: {e}")
            traceback.print_exc()
            sys.exit(1)
        finally:
            # Clean up sys.path
            if tmpdir in sys.path:
                sys.path.remove(tmpdir)

            # Unload imported temp modules so subsequent imports don't collide
            sys.modules.pop("api_client", None)
            sys.modules.pop("service", None)

        print()

    # Step 6: Teardown and Summary
    print("[6/6] Teardown complete. Sandboxed workspace deleted successfully.")
    print("-" * 80)
    print(" MIGRATION SUCCESSFUL: Synchronous Requests -> Asynchronous HTTPX ".center(80))
    print("-" * 80)


if __name__ == "__main__":
    main()
