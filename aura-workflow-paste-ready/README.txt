Aura workflow fix — paste-ready package

Fastest method
==============
1. Copy this whole folder into the ROOT of your Aura repository.
2. From the Aura repository root run:

   python aura-workflow-paste-ready/apply_workflow_fix.py

3. Then run the focused regression test:

   python -m unittest tests.test_workflow_regressions

What the installer changes
==========================
- aura/agent.py
  * restores project state when reopening a conversation
  * persists an approved first plan into SQLite plan_steps
  * stops reporting normal post-tool continuation as a retry
  * emits retry only at a real completion-gate retry
  * protects blocked plan steps from automatic completion

- aura/web_bridge.py
  * handles retry/stream_reset as stream-discard signals
  * clears its streamed-token bookkeeping when the UI stream resets

- tests/test_aura.py
  * updates the existing retry regression expectation

- tests/test_workflow_regressions.py
  * new focused regression coverage for the workflow fixes

Safety
======
The installer edits only exact source blocks. If your local files differ from the
version this patch was prepared against, it refuses to continue instead of doing a
fuzzy edit. Before each existing file is changed it creates a sibling backup ending
in .workflow-fix.bak.

Alternative
===========
If you prefer git, the original aura-workflow-fix.patch can be applied from the
repository root with:

   git apply --check aura-workflow-fix.patch
   git apply aura-workflow-fix.patch
