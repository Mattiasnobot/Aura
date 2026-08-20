Aura project-name / project-scope fix
=====================================

This is the follow-up fix for the observed run where:
  "Create a new project called shop ..."
created both `new/` and `shop/`, then reported validation evidence from `new/`.

What it changes
---------------
1. Explicit names such as "project called shop" / "project named shop" win.
2. Generic descriptions such as "new project" no longer invent a project called `new`.
3. During a project build, file mutations outside the selected project are refused
   before they execute, including nested tool calls inside execute_code.
4. validate_project must target the selected project rather than another folder.
5. The final evidence footer only lists files from the selected project.
6. Adds regression tests for the exact `new` vs `shop` failure.

Apply
-----
Put the `aura-project-scope-fix` folder in the root of your Aura repository, then run:

  python aura-project-scope-fix/apply_project_scope_fix.py

Then test:

  python -m unittest tests.test_project_scope_regressions

If you already installed the earlier workflow-continuation fix, keep it installed.
This follow-up edits different sections of agent.py and is designed to layer on top.

The installer creates:
  aura/agent.py.project-scope-fix.bak

It refuses to edit agent.py if the expected source blocks do not match exactly.
