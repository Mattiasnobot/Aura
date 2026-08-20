Aura live-progress fix

Built from the exact local files used by the current interactive/adaptive cards.

WHY
The live work card could stay at 0/N until completion because:
1. web_bridge._start_plan treated every non-empty plan line as a step, including
   ```tool_call fences and JSON tool-call text.
2. _on_tool only counted successful write tools, so real read/inspect/validate
   work never advanced the plan.
3. The browser had no event describing the successful tool activity it could show.

WHAT CHANGES
- aura/web_bridge.py
  * filters raw tool-call/code-fence syntax out of approved plans
  * classifies human steps as read / write / validate / research
  * ticks a step only when a successful matching tool proves it happened
  * emits work_activity after successful tools while a plan is active
- aura/web/app.js
  * listens for work_activity
  * shows the most recent successful action in the live card
  * labels progress as approved plan steps, not only files
  * uses Done rather than Ready for completed read/check steps

WHAT DOES NOT CHANGE
- agent prompts or reasoning
- tool execution
- permissions
- database/storage
- project parsing
- validation rules
- final result cards
- Workspace
- Aura Mind
- avatar
- styles.css / index.html

INSTALL
  python aura-live-progress-fix/apply_live_progress_fix.py

ROLLBACK
  python aura-live-progress-fix/apply_live_progress_fix.py --restore

The installer verifies SHA-256 against the exact current app.js/web_bridge.py it
was built from, backs up both before writing either, compiles web_bridge.py, and
runs `node --check` on app.js when Node is available. It restores both files
automatically if validation fails.
