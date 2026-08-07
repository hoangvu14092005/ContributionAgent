---
name: Code Review Patterns
description: Reusable reviewer heuristics for correctness, concurrency, APIs, tests, and operational failure modes
---

# Code Review Patterns

Look for state transitions that can be skipped, stale snapshots, mutable proof objects, broad exception catches, retries after partial side effects, ownership mismatches, incompatible API contracts, resource leaks, and tests that cannot fail when the implementation is broken.
