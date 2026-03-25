"""Review perspectives: pluggable review lenses for code review."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ReviewPerspective:
    """A focused review lens.

    Each perspective defines a specific angle from which to review code,
    including what to look for, how to prompt the reviewer, and optional
    severity weighting and language filtering.

    Args:
        name: Short identifier (e.g., "concurrency").
        focus_area: One-line description of what to look for.
        prompt_template: Full prompt for the reviewer. Uses {diff} placeholder.
        severity_weights: Optional mapping of severity names to weight multipliers.
        languages: List of languages this perspective applies to.
            None means it applies to all languages.
    """

    name: str
    focus_area: str
    prompt_template: str
    severity_weights: dict[str, float] = field(default_factory=dict)
    languages: list[str] | None = None


BUILTIN_PERSPECTIVES: dict[str, ReviewPerspective] = {
    "logic": ReviewPerspective(
        name="logic",
        focus_area="Correctness: off-by-one, null handling, error paths, return types",
        prompt_template=(
            "Review this diff for logical correctness. Focus on the following areas:\n"
            "\n"
            "1. OFF-BY-ONE ERRORS: Check loop bounds, slice indices, range boundaries, "
            "and fence-post conditions. Verify that iteration counts match the intended "
            "behavior and that boundary values are handled correctly.\n"
            "\n"
            "2. NULL/NONE HANDLING: Identify any value that could be None or null at "
            "runtime but is accessed without a guard. Check optional returns, dictionary "
            "lookups, attribute chains, and function parameters with default None.\n"
            "\n"
            "3. ERROR PATHS: Trace what happens when functions fail, exceptions are "
            "raised, or invalid input is provided. Ensure error conditions are handled "
            "rather than silently ignored. Check that resources are cleaned up on failure.\n"
            "\n"
            "4. RETURN TYPES: Verify that all code paths return the expected type. "
            "Watch for implicit None returns, inconsistent return types across branches, "
            "and missing return statements in conditional logic.\n"
            "\n"
            "5. STATE MUTATIONS: Check for unintended side effects, aliasing bugs where "
            "mutable objects are shared, and ordering dependencies between operations.\n"
            "\n"
            "For each issue found, report severity (info/suggestion/warning/error/critical), "
            "file path, line number, and a clear explanation of the bug and how to fix it.\n"
            "\n"
            "{diff}"
        ),
    ),
    "security": ReviewPerspective(
        name="security",
        focus_area="Injection, auth gaps, secrets in code, unsafe deserialization",
        prompt_template=(
            "Review this diff for security vulnerabilities. Focus on the following areas:\n"
            "\n"
            "1. INJECTION: Check for SQL injection, command injection, path traversal, "
            "XSS, and template injection. Any user-controlled input that reaches a "
            "dangerous sink without sanitization is critical.\n"
            "\n"
            "2. AUTHENTICATION AND AUTHORIZATION: Verify that endpoints check auth, "
            "that privilege escalation is not possible, and that access control is "
            "enforced consistently. Check for missing auth decorators or middleware.\n"
            "\n"
            "3. SECRETS IN CODE: Flag hardcoded API keys, passwords, tokens, private "
            "keys, or connection strings. These should come from environment variables "
            "or a secrets manager, never from source code.\n"
            "\n"
            "4. UNSAFE DESERIALIZATION: Check for pickle.loads, yaml.load without "
            "SafeLoader, eval/exec on untrusted input, or JSON parsing without "
            "validation. These can lead to remote code execution.\n"
            "\n"
            "5. CRYPTOGRAPHIC ISSUES: Flag weak algorithms (MD5, SHA1 for security), "
            "hardcoded IVs or salts, missing HTTPS enforcement, and improper random "
            "number generation for security-sensitive operations.\n"
            "\n"
            "Rate each finding as info/suggestion/warning/error/critical. Injection and "
            "RCE issues are always critical. Report file, line, and remediation steps.\n"
            "\n"
            "{diff}"
        ),
    ),
    "tests": ReviewPerspective(
        name="tests",
        focus_area="Test coverage, edge cases, assertion quality, mock appropriateness",
        prompt_template=(
            "Review this diff for test quality and coverage. Focus on the following areas:\n"
            "\n"
            "1. COVERAGE GAPS: Identify new code paths, branches, and functions that "
            "lack corresponding tests. Every public method and every conditional branch "
            "should have at least one test exercising it.\n"
            "\n"
            "2. EDGE CASES: Check whether tests cover boundary conditions, empty inputs, "
            "None values, maximum sizes, concurrent access, and error scenarios. Tests "
            "that only cover the happy path are insufficient.\n"
            "\n"
            "3. ASSERTION QUALITY: Verify that assertions are specific and meaningful. "
            "Assertions like 'assert result is not None' are weak. Prefer exact value "
            "checks, structural assertions, and negative assertions (things that should "
            "NOT be present).\n"
            "\n"
            "4. MOCK APPROPRIATENESS: Check that mocks are used judiciously. Over-mocking "
            "makes tests brittle and disconnected from real behavior. Under-mocking makes "
            "tests slow and flaky. External services should be mocked; internal logic "
            "generally should not.\n"
            "\n"
            "5. TEST ISOLATION: Ensure tests do not depend on execution order, shared "
            "mutable state, or external resources without proper setup/teardown.\n"
            "\n"
            "Report each finding with severity, file, line, and concrete suggestions for "
            "what tests to add or improve.\n"
            "\n"
            "{diff}"
        ),
    ),
    "architecture": ReviewPerspective(
        name="architecture",
        focus_area="Naming, separation of concerns, dependency direction, patterns",
        prompt_template=(
            "Review this diff for architectural quality. Focus on the following areas:\n"
            "\n"
            "1. NAMING: Check that classes, functions, variables, and modules have clear, "
            "descriptive names that follow project conventions. Names should reveal intent "
            "without requiring comments. Flag abbreviations, generic names (data, info, "
            "manager), and misleading names.\n"
            "\n"
            "2. SEPARATION OF CONCERNS: Verify that each module, class, and function has "
            "a single, well-defined responsibility. Flag god classes, functions that mix "
            "business logic with I/O, and modules that combine unrelated functionality.\n"
            "\n"
            "3. DEPENDENCY DIRECTION: Check that dependencies flow from higher layers to "
            "lower layers, not the reverse. Domain logic should not depend on framework "
            "details. Flag circular imports and inappropriate coupling between modules.\n"
            "\n"
            "4. DESIGN PATTERNS: Identify misapplied patterns (over-engineering), missing "
            "patterns where they would simplify code, and anti-patterns like god objects, "
            "feature envy, or shotgun surgery.\n"
            "\n"
            "5. API DESIGN: Check that public interfaces are minimal, consistent, and "
            "hard to misuse. Verify backward compatibility is maintained where needed.\n"
            "\n"
            "Report findings with severity, file, line, and suggestions for restructuring.\n"
            "\n"
            "{diff}"
        ),
    ),
    "concurrency": ReviewPerspective(
        name="concurrency",
        focus_area="Race conditions, deadlocks, shared mutable state, atomic operations",
        prompt_template=(
            "Review this diff for concurrency issues. Focus on the following areas:\n"
            "\n"
            "1. RACE CONDITIONS: Identify shared mutable state that is accessed from "
            "multiple threads or async tasks without proper synchronization. Check for "
            "check-then-act patterns, read-modify-write sequences, and time-of-check "
            "to time-of-use (TOCTOU) bugs.\n"
            "\n"
            "2. DEADLOCKS: Check for nested lock acquisition that could deadlock, "
            "inconsistent lock ordering, and holding locks while performing blocking "
            "I/O or waiting on other resources.\n"
            "\n"
            "3. SHARED MUTABLE STATE: Flag global variables, class-level mutables, and "
            "default mutable arguments that could be modified concurrently. Prefer "
            "thread-local storage, immutable data, or message passing.\n"
            "\n"
            "4. ATOMIC OPERATIONS: Verify that compound operations that must be atomic "
            "are properly protected. Dictionary updates, list modifications, and counter "
            "increments are not atomic in most languages.\n"
            "\n"
            "5. ASYNC SAFETY: For async code, check for blocking calls in async "
            "functions, missing awaits, and improper task cancellation handling. Verify "
            "that async context managers and cleanup are correct.\n"
            "\n"
            "Rate each finding by severity. Race conditions and deadlocks are typically "
            "error or critical. Report file, line, and recommended synchronization fix.\n"
            "\n"
            "{diff}"
        ),
    ),
    "performance": ReviewPerspective(
        name="performance",
        focus_area="Algorithmic complexity, unnecessary allocations, N+1 queries, caching",
        prompt_template=(
            "Review this diff for performance issues. Focus on the following areas:\n"
            "\n"
            "1. ALGORITHMIC COMPLEXITY: Identify loops with O(n^2) or worse complexity, "
            "especially nested iterations over large collections. Check for repeated "
            "linear searches that could use a set or dict, and string concatenation in "
            "loops that should use join or a builder.\n"
            "\n"
            "2. UNNECESSARY ALLOCATIONS: Flag creation of large temporary objects, "
            "repeated list/dict copies, excessive string formatting, and object creation "
            "inside hot loops. Check for generators versus list comprehensions where "
            "only iteration is needed.\n"
            "\n"
            "3. N+1 QUERIES: For database or API access patterns, check for queries "
            "inside loops that could be batched. One query per item in a collection is "
            "almost always a performance bug.\n"
            "\n"
            "4. CACHING OPPORTUNITIES: Identify expensive computations or I/O operations "
            "that are repeated with the same inputs. Suggest memoization, caching, or "
            "precomputation where appropriate.\n"
            "\n"
            "5. I/O EFFICIENCY: Check for synchronous I/O that blocks the event loop, "
            "missing connection pooling, unbuffered reads/writes, and failure to use "
            "streaming for large payloads.\n"
            "\n"
            "Rate findings by severity based on expected impact. Report file, line, "
            "the problematic pattern, and a concrete optimization suggestion.\n"
            "\n"
            "{diff}"
        ),
    ),
    "type_safety": ReviewPerspective(
        name="type_safety",
        focus_area="Type narrowing, Any escape hatches, missing annotations, generics",
        prompt_template=(
            "Review this diff for type safety issues. Focus on the following areas:\n"
            "\n"
            "1. MISSING ANNOTATIONS: Check that all function parameters, return types, "
            "and class attributes have type annotations. Public APIs should always be "
            "fully annotated. Flag bare function signatures without types.\n"
            "\n"
            "2. ANY ESCAPE HATCHES: Identify uses of Any, cast(), type: ignore, and "
            "other mechanisms that bypass the type system. Each one should have a clear "
            "justification. Flag unnecessary uses that could be replaced with proper "
            "generic types or protocols.\n"
            "\n"
            "3. TYPE NARROWING: Check that isinstance checks, None guards, and "
            "discriminated unions are used correctly to narrow types before access. "
            "Flag attribute access on union types without narrowing.\n"
            "\n"
            "4. GENERICS AND PROTOCOLS: Verify that generic classes and functions use "
            "TypeVar correctly, that Protocol classes define the minimal interface, and "
            "that type parameters are properly constrained.\n"
            "\n"
            "5. RUNTIME TYPE MISMATCHES: Identify places where runtime values could "
            "diverge from declared types, such as JSON parsing without validation, "
            "dynamic attribute access, and untyped third-party library returns.\n"
            "\n"
            "Rate findings by severity. Missing annotations are suggestions; Any abuse "
            "and type mismatches are warnings or errors. Report file, line, and fix.\n"
            "\n"
            "{diff}"
        ),
    ),
    "error_handling": ReviewPerspective(
        name="error_handling",
        focus_area="Exception granularity, propagation, recovery paths, user messages",
        prompt_template=(
            "Review this diff for error handling quality. Focus on the following areas:\n"
            "\n"
            "1. EXCEPTION GRANULARITY: Check that exceptions are specific rather than "
            "catching bare Exception or BaseException. Each except clause should handle "
            "a specific failure mode. Flag broad except blocks that swallow errors.\n"
            "\n"
            "2. ERROR PROPAGATION: Verify that errors are propagated to callers who can "
            "handle them meaningfully. Check for silenced exceptions (except: pass), "
            "lost error context (raise NewError without __cause__), and errors that are "
            "logged but not acted upon.\n"
            "\n"
            "3. RECOVERY PATHS: Identify operations that can fail but lack recovery "
            "logic. File I/O, network calls, and subprocess invocations should have "
            "explicit error handling with fallback behavior or clean failure.\n"
            "\n"
            "4. USER-FACING MESSAGES: Check that error messages are helpful and "
            "actionable. Messages should explain what went wrong, why, and what the "
            "user can do about it. Flag generic messages like 'An error occurred' and "
            "messages that expose internal implementation details.\n"
            "\n"
            "5. RESOURCE CLEANUP: Verify that resources (files, connections, locks) are "
            "properly released on error using context managers, try/finally, or "
            "equivalent mechanisms. Flag resources opened without cleanup guarantees.\n"
            "\n"
            "Rate findings by severity. Swallowed exceptions and missing cleanup are "
            "errors. Report file, line, and suggested error handling improvement.\n"
            "\n"
            "{diff}"
        ),
    ),
}
