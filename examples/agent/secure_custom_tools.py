#!/usr/bin/env python3
"""Custom tool authoring with dynamic security gates and confirmation policies.

This example showcases how to:
1. Define a custom database tool using BaseTool.
2. Implement a custom SecurityAnalyzer to dynamically evaluate risk.
3. Configure a custom PermissionPolicy using ConfirmAboveThreshold.
4. Programmatically intercept and resolve PendingApproval instances in headless environments.
5. Print and inspect the final AuditLog.

Usage:
    python examples/agent/secure_custom_tools.py
"""
from __future__ import annotations

import os
import sys
from typing import Any

# Ensure parent directory is in path for standalone execution
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import threading
from typing import TYPE_CHECKING

from chimera import (
    Agent,
    ConfirmAboveThreshold,
    LoopConfig,
    PermissionAction,
    ReAct,
    SecurityRisk,
)
from chimera.core.tool import BaseTool
from chimera.permissions.audit import AuditLog
from chimera.permissions.base import PermissionPolicy
from chimera.security.analyzer import SecurityAnalyzer
from chimera.types import Message, ToolCall, ToolResult
from chimera.providers.base import Provider, Response

if TYPE_CHECKING:
    from chimera.providers.thinking import ThinkingLevel


# ===========================================================================
# 1. Custom Tool Definition
# ===========================================================================

class DatabaseSchemaTool(BaseTool):
    """Executes SQL commands against a database schema."""

    name = "db_schema_op"
    description = "Execute SQL commands on the database schema (e.g. SELECT, ALTER TABLE, DROP TABLE)."
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The exact SQL statement to execute.",
            }
        },
        "required": ["query"],
    }
    is_read_only = False
    is_destructive = True

    def execute(self, args: dict[str, Any], env: Any) -> ToolResult:
        query = args.get("query", "")
        # Real implementation would call database connection pool here.
        # For demonstration purposes, we mock a successful execution.
        return ToolResult(
            output=f"Success: Query '{query}' completed successfully on database.",
            metadata={"row_count": 0, "status": "COMMAND_OK"},
        )


# ===========================================================================
# 2. Dynamic Security Analyzer
# ===========================================================================

class DatabaseSecurityAnalyzer(SecurityAnalyzer):
    """Dynamically parses SQL statements to classify command risk."""

    def analyze(self, tool_call: ToolCall) -> SecurityRisk:
        query = str(tool_call.arguments.get("query", "")).strip().upper()

        if not query:
            return SecurityRisk.LOW

        # 1. High risk: Destructive operations on entire tables/databases
        if any(cmd in query for cmd in ["DROP TABLE", "DROP DATABASE", "TRUNCATE"]):
            return SecurityRisk.HIGH

        # 2. Medium risk: Altering schemas, index changes, inserting/updating data
        if any(cmd in query for cmd in ["ALTER TABLE", "CREATE TABLE", "INSERT", "UPDATE", "DELETE"]):
            return SecurityRisk.MEDIUM

        # 3. Low risk: Harmless read-only operations
        if query.startswith("SELECT") or query.startswith("SHOW"):
            return SecurityRisk.LOW

        return SecurityRisk.UNKNOWN


# ===========================================================================
# 3. Custom Permission Policy (Security Gate)
# ===========================================================================

class SecureDatabaseGate(PermissionPolicy):
    """Permission policy combining database risk analysis and threshold gates."""

    def __init__(
        self,
        analyzer: SecurityAnalyzer,
        policy: ConfirmAboveThreshold,
        audit_log: AuditLog | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.policy = policy
        self.audit_log = audit_log

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
        # Wrap the incoming tool arguments into a standard ToolCall object
        tc = ToolCall(id="gate_eval", name=tool_name, arguments=args)

        # Analyze risk dynamically using our custom analyzer
        risk = self.analyzer.analyze(tc)

        # Let the threshold confirmation policy decide whether to prompt the operator
        if self.policy.should_confirm(risk):
            print(f"    [GATEKEEPER] Tool '{tool_name}' returned risk level: {risk.name}. Requesting user confirmation.")
            return PermissionAction.ASK

        print(f"    [GATEKEEPER] Tool '{tool_name}' returned risk level: {risk.name}. Auto-allowing execution.")
        if self.audit_log:
            self.audit_log.record(
                tool_name=tool_name,
                arguments=args,
                decision="auto_approved",
                reason=f"Risk level: {risk.name}. Below confirmation threshold.",
            )
        return PermissionAction.ALLOW


# ===========================================================================
# 4. Mock LLM Provider
# ===========================================================================

class MockProvider(Provider):
    """Simulates an agent issuing three database commands of increasing risk levels."""

    def __init__(self) -> None:
        self.step_index = 0
        self.queries = [
            "SELECT * FROM users LIMIT 10",
            "ALTER TABLE users ADD COLUMN phone TEXT",
            "DROP DATABASE production",
        ]

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: ThinkingLevel | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Response:
        # If we have run through all tasks, terminate loop
        if self.step_index >= len(self.queries):
            return Response(
                content="All database operations have been evaluated.",
                tool_calls=[],
                usage={"input_tokens": 100, "output_tokens": 50},
            )

        query = self.queries[self.step_index]
        self.step_index += 1

        # Instruct the agent to call our custom DatabaseSchemaTool
        return Response(
            content=f"Evaluating SQL operation #{self.step_index}: {query}",
            tool_calls=[
                ToolCall(
                    id=f"call_{self.step_index}",
                    name="db_schema_op",
                    arguments={"query": query},
                )
            ],
            usage={"input_tokens": 200, "output_tokens": 100},
        )

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "mock-secure-db-model"


# ===========================================================================
# 5. Execution Orchestration
# ===========================================================================

def main() -> None:
    print("=====================================================================")
    print("  Chimera Example: Secure Tool Authoring & Dynamic Permission Gates  ")
    print("=====================================================================\n")

    # 1. Instantiate security infrastructure
    analyzer = DatabaseSecurityAnalyzer()
    
    # Require human-in-the-loop confirmation for MEDIUM and HIGH risk levels
    confirm_policy = ConfirmAboveThreshold(threshold=SecurityRisk.MEDIUM, confirm_unknown=True)
    
    audit_log = AuditLog()
    gatekeeper = SecureDatabaseGate(analyzer, confirm_policy, audit_log)

    # 2. Configure Agent Loop
    config = LoopConfig(
        permissions=gatekeeper,
        audit_log=audit_log,
    )
    
    provider = MockProvider()
    custom_tools = [DatabaseSchemaTool()]
    
    agent = Agent(
        provider=provider,
        tools=custom_tools,
        loop=ReAct(max_steps=5, config=config),
    )

    print("--- Starting head-less programmatic agent execution ---")
    
    # 3. Run step-by-step to intercept & programmatically resolve approvals
    generator = agent.iter_steps("Check and maintain database schema.", env=None)
    try:
        step = next(generator)
        while True:
            print(f"\n[Step {step.step}] Assistant: {step.message.content if step.message else 'Running...'}")
            
            # Check for interactive confirmation request (PermissionAction.ASK)
            if step.pending_approval:
                pa = step.pending_approval
                query = pa.arguments.get("query", "")
                
                print("--> PENDING APPROVAL REQUEST RECEIVED:")
                print(f"    Tool:      {pa.tool_name}")
                print(f"    Query:     {query}")
                
                # Programmatically simulate approval decisions
                if "ALTER TABLE" in query:
                    print("    [DECISION] Programmatic review: ALTER TABLE is approved for development.")
                    pa.approve()
                    audit_log.record(
                        tool_name=pa.tool_name,
                        arguments=pa.arguments,
                        decision="approved",
                        reason="Programmatic review: ALTER TABLE is approved for development.",
                    )
                    audit_log.record(
                        tool_name=pa.tool_name,
                        arguments=pa.arguments,
                        decision="allowed",
                        reason="Approved by user review.",
                    )
                elif "DROP DATABASE" in query:
                    print("    [DECISION] Programmatic review: DROP DATABASE is DANGEROUS! Rejecting request.")
                    pa.deny("Access Denied: Destruction of production databases is prohibited.")
                    audit_log.record(
                        tool_name=pa.tool_name,
                        arguments=pa.arguments,
                        decision="denied",
                        reason="Access Denied: Destruction of production databases is prohibited.",
                    )
                else:
                    print("    [DECISION] Programmatic review: Safety policy denies unknown command.")
                    pa.deny("Auto-denied by system safety rules.")
                    audit_log.record(
                        tool_name=pa.tool_name,
                        arguments=pa.arguments,
                        decision="denied",
                        reason="Auto-denied by system safety rules.",
                    )

            # Advance loop
            step = generator.send(None)
            
    except StopIteration as e:
        result = e.value
        print("\n=== Agent Result ===")
        print(f"Success: {result.success}")
        print(f"Steps:   {result.steps}")
        print(f"Output:  {result.output}")

    # 4. Print and review final AuditLog summary
    print("\n=====================================================================")
    print("  Security Audit Log Analysis                                       ")
    print("=====================================================================")
    
    print("\nSummary Counts:")
    for decision, count in audit_log.summary().items():
        print(f"  - {decision.upper()}: {count}")

    print("\nDetailed Logs:")
    for entry in audit_log.entries:
        print(f"  [{entry.time_str}] Tool: '{entry.tool_name}' | Decision: {entry.decision.upper()}")
        print(f"    Arguments: {entry.arguments}")
        if entry.reason:
            print(f"    Reason:    {entry.reason}")
    print("\n=====================================================================")


if __name__ == "__main__":
    main()
