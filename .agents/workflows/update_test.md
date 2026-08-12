---
name: Update Test
description: Add or repair regression tests for a concrete behavior change without weakening assertions
trigger: /update_test
inputs:
  - name: TARGET
    description: File, behavior, failure, or test to update
---

# Update Test

Identify the behavior contract first. Reproduce the failure, add the smallest regression test that fails for the old behavior, implement or preserve the fix, and run the targeted test plus neighboring suites. Do not make a failing test pass by deleting coverage or weakening meaningful assertions.
