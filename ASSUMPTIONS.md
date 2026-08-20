# Assumptions

- Python 3.10 or newer and a normal desktop web browser are available.
- The browser exposes the standard Canvas 2D API, which Aura uses locally for its depth-projected digital-human avatar; no avatar data is sent over the network.
- “OS-level” means a local desktop process with deliberately constrained filesystem and subprocess capabilities, not unrestricted system access.
- File-agent capabilities are exposed as a tested Python API; the chat MVP offers the common list/read/build paths without attempting to parse arbitrary natural-language file edits.
- Command approval is explicit and deliberately cannot be bypassed through chat text. The user may reuse permission only for an identical command or URL during the current task; any changed argument asks again.
- “Powerful” autonomy means broader planning and safe workspace tools, not silent unrestricted machine access. Executable project code, package operations, external networking, and desktop launches keep a visible approval boundary.
- Personal learning uses only clear first-person statements made to Aura, never assistant guesses. Credentials and sensitive traits are excluded, automatic learning has a Settings toggle, and every accepted memory is locally visible, editable, pinnable, and forgettable.
- Workspace file and folder operations never use the command runner; their dedicated sandbox tools do not require command approval.
- LM Studio runs locally and exposes an OpenAI-compatible server; Aura does not fall back to a cloud model.
- Tool quality depends on the selected model. Models trained for function/tool calling generally build more reliably.
- Voice input is optional because reliable offline recognition requires platform-specific microphone dependencies. Its absence is handled gracefully; local speech output uses Piper neural TTS with Windows SAPI as a fallback.
- Resizable panel state and action-log visibility are local UI preferences stored with Aura's other protected settings.
- Aura Mind is a local visualization of already stored state, not a separate semantic database; it never reads file contents or sends graph data outside the computer.
- “HTML only” means HTML/CSS/JavaScript is Aura's sole user interface. Python remains the local security and agent backend because browser JavaScript must not receive unrestricted file or command authority.
- Closing the browser tab leaves the local service running so active work is not killed accidentally. The visible **Quit Aura** button stops it deliberately.
