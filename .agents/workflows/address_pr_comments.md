---
name: Address PR Comments
description: Resolve actionable pull request review feedback and verify each requested change
trigger: /address_pr_comments
inputs:
  - name: PR_URL
    description: Pull request to inspect
---

# Address PR Comments

1. Read unresolved review threads and requested changes.
2. Separate actionable defects from questions, preferences, and already-resolved comments.
3. Apply the smallest safe code changes for actionable feedback only.
4. Add or update regression tests for behavior changes.
5. Run targeted tests, lint, and relevant architecture or safety checks.
6. Re-read the diff against each review comment before marking work complete.
7. Never dismiss or resolve reviewer feedback without evidence that the requested change is addressed.
