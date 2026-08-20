"""Images and screens.

Lifted out of `agent.py`, where 516 lines of handlers sat interleaved with the
turn loop and the completion gates. These are leaf code: each does one thing and
returns a result. They stay methods because they really do use `self.sandbox`,
`self.memory` and `self.config` — a mixin keeps every `self.` resolving exactly
as it did, which is what makes this a move and not a rewrite.
"""

from __future__ import annotations

from .image_diff import compare_images
from .toolkit import tool


class MediaTools:
    """Images and screens."""

    @tool('look_at_image', 'Actually look at a workspace image (PNG/JPEG/GIF/WebP/BMP). The image is attached to the conversation so you can describe or compare what it shows. Call this whenever the user asks what an image contains or looks like — listing or reading the file cannot answer that, because its pixels are only visible through this tool.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['path'])
    def _tool_look_at_image(self, name, args, approve, call):
        if not self.vision_enabled():
            raise ValueError(
                "The loaded model does not accept images. Turn vision on in "
                "Settings if you know it does.")
        result = self._read_image_attachment(str(args["path"]))
        return result

    @tool('capture_page', "Render a workspace HTML page in a local headless browser and save a PNG screenshot of it into the workspace. Use this to see how a page actually looks, then call look_at_image on the saved screenshot. Needs the user's approval because it launches a browser.",
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'width': {'type': 'integer', 'minimum': 320, 'maximum': 2560, 'default': 1200}, 'height': {'type': 'integer', 'minimum': 240, 'maximum': 2000, 'default': 800}}, ['path'])
    def _tool_capture_page(self, name, args, approve, call):
        result = self._capture_page(
            str(args["path"]), approve,
            int(args.get("width", 1200)), int(args.get("height", 800)))
        # The wrapper reads "ok" straight from the result now.
        result["ok"] = bool(result.get("approved"))
        return result

    @tool('compare_images', 'Measure exactly how two workspace PNG images differ: percentage of changed pixels and the region that changed. Use it to check a render against a reference or to detect a layout regression between two screenshots. This is a real pixel measurement, not an impression.',
          {'first': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'second': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'tolerance': {'type': 'integer', 'minimum': 0, 'maximum': 128, 'default': 8}}, ['first', 'second'])
    def _tool_compare_images(self, name, args, approve, call):
        result = compare_images(
            self.sandbox.path(str(args["first"])),
            self.sandbox.path(str(args["second"])),
            tolerance=int(args.get("tolerance", 8)))
        return result
