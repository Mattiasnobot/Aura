Aura interactive work card — conservative UI pass

Based on the exact local files you uploaded on 2026-08-20.

CHANGED
  aura/web/app.js
  aura/web/styles.css

INTENTIONALLY UNCHANGED
  aura/web/index.html
  aura/web_bridge.py

Why no backend change?
Your current bridge already emits the project, approved file plan, plan progress,
plan finish, state changes, tool/activity logs, and final task journal. This pass
only combines those existing facts into a better UI.

What changes
- The small bottom plan strip becomes a larger live work card while Aura is building.
- It shows the active project, current phase, progress percentage, planned files,
  Ready / Working / Pending states, Open workspace, and Stop.
- When the final reply arrives, the live card disappears and becomes a compact
  result card attached to Aura's reply.
- Result cards show duration, changed workspace items, validation status, clickable
  file chips, a collapsed human-friendly activity list, Workspace/Details/Repeat/Undo.
- Batch write_files entries are counted correctly in the result card.

What does NOT change
- Agent prompts
- Tool routing
- Project parsing
- Validation rules
- Permissions
- Task execution
- Database/storage
- Aura face/avatar
- Workspace explorer
- HTML structure
- Python bridge behavior

Install
1. Put/extract this folder either inside the Aura repo or next to the Aura folder.
2. Run:
       python aura-interactive-work-card/apply_interactive_work_card.py
   If your terminal is already inside this package folder, this also works:
       python apply_interactive_work_card.py
3. Restart Aura.
4. Ask Aura to build a small project with an approved file plan.

Rollback
       python aura-interactive-work-card/apply_interactive_work_card.py --restore
or, from this package folder:
       python apply_interactive_work_card.py --restore

Safety
The installer checks SHA-256 against the exact app.js/styles.css you uploaded.
If either local file has changed since then, it refuses to overwrite it.
Both originals are backed up before either replacement is written.
If Node is installed, the installer runs `node --check` and restores originals
automatically if JavaScript parsing fails.
