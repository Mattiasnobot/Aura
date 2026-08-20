Aura work-card project-title hotfix

Targets the interactive work-card version installed previously.

What it fixes
- A finished card could say `new completed` even when the actual changed files were under `shop/`.
- The card now derives the project from changed task paths first.
- A path root must account for at least 60% of nested changed paths before it wins, so one stray path cannot hijack the title.
- If path evidence is inconclusive, it falls back to task.project, then the conversation project.

Changed
  aura/web/app.js only

Not changed
  styles.css
  index.html
  web_bridge.py
  agent/workflow logic

Install from the Aura repo root:
  python aura-work-card-project-title-fix/apply_project_title_fix.py

The installer checks the exact SHA-256 of the previous interactive-work-card app.js and refuses to overwrite a different file.
