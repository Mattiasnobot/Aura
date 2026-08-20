Aura adaptive result cards

Built on the exact UI state after:
1. aura-interactive-work-card
2. aura-work-card-project-title-fix

CHANGED
  aura/web/app.js
  aura/web/styles.css

UNCHANGED
  aura/web/index.html
  aura/web_bridge.py
  all agent/workflow/backend files

What changes
- Read-only validation tasks render as VERIFIED RESULT rather than FINISHED WORK.
- A validation of `shop` can render `shop verified` even with no changed files.
- Verification cards can show files checked, issue count, No files modified, and Validated.
- Build/edit tasks retain the finished-work presentation.
- Exact `Confirmed evidence:` footer sections are removed from the visible reply and moved into an expandable Evidence section on the result card.
- Duplicate evidence lines are deduplicated.
- `Not confirmed:` and `Read from the network:` sections are never hidden by this UI cleanup.
- The original complete reply still exists in backend/session history; this is presentation only.

Install
  python aura-adaptive-result-cards/apply_adaptive_result_cards.py

Rollback
  python aura-adaptive-result-cards/apply_adaptive_result_cards.py --restore

Safety
- SHA-256 checks require the exact currently expected UI files.
- If your local UI changed after the project-title hotfix, installation refuses rather than overwriting it.
- Both files are backed up before either is changed.
- If Node is installed, app.js is syntax-checked and automatically restored on failure.
