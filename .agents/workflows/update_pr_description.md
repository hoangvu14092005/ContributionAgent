---
name: Update PR Description
description: Refresh a pull request description so it accurately reflects the final code and validation
trigger: /update_pr_description
inputs:
  - name: PR_URL
    description: Pull request whose description should be refreshed
---

# Update PR Description

Summarize the final diff rather than the original intent. Include the problem, implementation, user or developer impact, validation performed, remaining limitations, and linked issues. Do not claim tests or behavior that were not verified.
