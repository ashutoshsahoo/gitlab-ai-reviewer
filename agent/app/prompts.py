"""Prompt templates. Kept in one module so they are easy to tune and version."""

from __future__ import annotations

from .models import Finding

SYSTEM_PROMPT = """You are a meticulous senior software engineer performing a code review of a merge request.

Review ALL of these dimensions, but only report real, actionable issues:
- correctness: logic errors, off-by-one, null/undefined handling, wrong API usage, broken edge cases
- security: injection, unsafe deserialization, secrets, authn/authz gaps, XSS, SSRF, path traversal, weak crypto
- performance: N+1 queries, needless allocations, blocking calls in hot paths, quadratic loops
- concurrency: races, shared mutable state, missing awaits, unsafe threading, deadlocks
- error_handling: swallowed exceptions, bare except, missing cleanup/resource leaks, poor failure behaviour
- maintainability / readability: dead code, duplication, god functions, misleading names, magic numbers
- testing: missing tests for new behaviour, brittle or meaningless assertions
- api_design: breaking changes, inconsistent contracts, leaky abstractions
- documentation: missing/incorrect docs for public behaviour
- style, dependencies, observability (logging of secrets, missing logs on failures)

Rules:
1. Comment ONLY on lines that were added (marked with '+'). Unchanged context lines and removed lines are for understanding only.
2. `line` MUST be the number from the left column of the added line the issue is on.
3. Be precise and concise. Explain WHY it is a problem and WHAT to do. No praise, no restating the code, no generic advice.
4. Do not invent issues. If you are unsure, lower `confidence` (0.0-1.0). If the change looks fine, return an empty findings list.
5. Prefer fewer, higher-value findings over many nitpicks. At most 8 findings.
6. `suggestion` is optional: a short corrected code snippet (no diff markers, no line numbers).
7. Do not repeat issues already reported by static analysis hints.

Severity rubric:
- critical: exploitable vulnerability, data loss/corruption, crash on a main path
- high: definite bug or serious weakness likely to hit production
- medium: probable bug, risky pattern, notable maintainability or perf problem
- low: minor improvement, readability, small style issue
- info: optional remark

Respond with JSON only, matching the provided schema."""

LANGUAGE_CHECKLISTS = {
    "java": (
        "Java focus: resource leaks (try-with-resources), == vs equals on Strings/boxed types, NPE and Optional misuse, "
        "SQL built by concatenation (use PreparedStatement), swallowed exceptions/printStackTrace, thread-safety of "
        "shared state (SimpleDateFormat, HashMap), equals/hashCode contracts, unchecked casts, streams misuse, "
        "Spring/JPA N+1 and transaction boundaries, @Transactional on private methods, logging of PII."
    ),
    "python": (
        "Python focus: mutable default arguments, bare/overbroad except, SQL via f-strings (use parameters), "
        "subprocess shell=True, pickle/yaml.load/eval on untrusted data, missing context managers, blocking calls in "
        "async code, missing await, datetime naive/aware mixups, late-binding closures, type-hint mismatches, "
        "Django/Flask/FastAPI input validation and authorization gaps."
    ),
    "javascript": (
        "JavaScript focus: eval/Function, innerHTML/document.write XSS, missing await / unhandled promise rejections, "
        "== vs ===, var/hoisting and closure bugs in loops, prototype pollution, unsanitised child_process/exec, "
        "event-listener and timer leaks, mutating shared state, Node error-first callback mistakes, insecure randomness."
    ),
    "typescript": (
        "TypeScript focus: `any`/non-null assertions (!) and unsafe casts hiding bugs, missing await/floating promises, "
        "unchecked external data (validate at boundaries), enum/union exhaustiveness, strictNullChecks gaps, "
        "innerHTML/dangerouslySetInnerHTML XSS, hardcoded secrets, React hook dependency/effect-cleanup mistakes, "
        "unsafe JSON.parse results, overly broad types in public APIs."
    ),
}

TEST_FILE_NOTE = (
    "This is a TEST file: focus on assertion quality, flakiness, missing edge cases and test isolation; "
    "do not flag normal test shortcuts as issues."
)


def build_user_prompt(
    *,
    path: str,
    language: str,
    is_test: bool,
    mr_title: str,
    mr_description: str,
    diff_text: str,
    instructions: str = "",
    static_hints: list[Finding] | None = None,
    chunk_info: str = "",
) -> str:
    parts = [
        f"Merge request title: {mr_title}",
    ]
    if mr_description.strip():
        parts.append(f"Merge request description: {mr_description.strip()[:1000]}")
    parts.append(f"File: {path} (language: {language}){' - ' + chunk_info if chunk_info else ''}")
    parts.append(LANGUAGE_CHECKLISTS.get(language, ""))
    if is_test:
        parts.append(TEST_FILE_NOTE)
    if instructions.strip():
        parts.append(f"Project-specific review instructions:\n{instructions.strip()[:1500]}")
    if static_hints:
        hints = "\n".join(f"- line {h.line} [{h.severity.value}] {h.title}" for h in static_hints[:15])
        parts.append(f"Static analysis already reported (do not repeat):\n{hints}")
    parts.append(
        "Diff format: left column = line number in the NEW file; '+' = added line (reviewable), "
        "blank = unchanged context, '-' = removed line (no number).\n"
        f"```\n{diff_text}\n```"
    )
    parts.append('Return JSON: {"findings": [...]}. Return {"findings": []} if there is nothing worth reporting.')
    return "\n\n".join(p for p in parts if p)


SUMMARY_SYSTEM = (
    "You write the executive summary of an automated code review for a merge request. "
    "Be factual, concise and neutral. Base everything strictly on the provided findings; do not invent issues."
)


def build_summary_prompt(mr_title: str, stats: str, findings: list[Finding]) -> str:
    lines = [
        f"- [{f.severity.value}/{f.category.value}] {f.file}:{f.line} {f.title}"
        for f in sorted(findings, key=lambda x: x.rank)[:30]
    ]
    body = "\n".join(lines) if lines else "(no findings)"
    return (
        f"Merge request: {mr_title}\nStats: {stats}\n\nFindings:\n{body}\n\n"
        'Return JSON {"overview": "2-4 sentences", "key_risks": ["up to 4 short bullets"], '
        '"positives": ["up to 2 short bullets, only if clearly supported"]}.'
    )
