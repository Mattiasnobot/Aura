"""Everything that leaves the machine, and the granted folders.

Lifted out of `agent.py`, where 516 lines of handlers sat interleaved with the
turn loop and the completion gates. These are leaf code: each does one thing and
returns a result. They stay methods because they really do use `self.sandbox`,
`self.memory` and `self.config` — a mixin keeps every `self.` resolving exactly
as it did, which is what makes this a move and not a rewrite.
"""

from __future__ import annotations

from .toolkit import tool


class OutsideTools:
    """Everything that leaves the machine, and the granted folders."""

    @tool('search_web',
          'Search the web through the SearXNG instance the user runs on this machine. '
          'Returns titles, links, and the snippet the engine already produced. It returns '
          'snippets only and never opens the result pages, so never describe what a linked '
          'page says as if you had read it — say the snippet said it. Refuses, with a reason, '
          'when the user has no search service configured or running.',
          {'query': {'type': 'string'},
           'count': {'type': 'integer', 'minimum': 1, 'maximum': 8, 'default': 5}}, ['query'])
    def _tool_search_web(self, name, args, approve, call):
        return self._search_web(str(args["query"]), int(args.get("count", 5)))

    @tool('http_get', 'Fetch a bounded HTTP(S) text response. Localhost is direct; any other domain must already be granted by the user under Permissions, and cannot be requested from here.',
          {'url': {'type': 'string'}, 'timeout': {'type': 'number', 'minimum': 1, 'maximum': 20, 'default': 10}}, ['url'])
    def _tool_http_get(self, name, args, approve, call):
        result = self._http_get(str(args["url"]),
                                max(1.0, min(float(args.get("timeout", 10)), 20.0)))
        return result

    @tool('read_external_file', "Read a UTF-8 text file inside a folder the user has already granted. Fails if there is no active permission for that file's folder.",
          {'path': {'type': 'string'}}, ['path'])
    def _tool_read_external_file(self, name, args, approve, call):
        result = {"path": str(args["path"]),
                  "content": self.external.read_file(str(args["path"]))}
        return result

    @tool('write_external_file', 'Write a UTF-8 text file inside a folder the user granted for writing. The previous version is saved first, so the change can be undone. A read grant is not enough; writing needs its own permission.',
          {'path': {'type': 'string'}, 'content': {'type': 'string'}}, ['path', 'content'], mutating=True)
    def _tool_write_external_file(self, name, args, approve, call):
        result = self.external_writer.write_file(
            str(args["path"]), str(args["content"]),
            task_id=self.current_task_id)
        return result

    @tool('list_external_folder', 'List files inside a folder the user has already granted. Fails if there is no active permission for that folder.',
          {'path': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500, 'default': 200}}, ['path'])
    def _tool_list_external_folder(self, name, args, approve, call):
        result = {"path": str(args["path"]),
                  "files": self.external.list_files(
                      str(args["path"]), limit=int(args.get("limit", 200)))}
        return result

    @tool('list_granted_folders', 'List folders outside the workspace that the user has granted Aura permission to read. Aura cannot grant itself access; only the user can, from the Permissions panel.',
          {}, [])
    def _tool_list_granted_folders(self, name, args, approve, call):
        result = {"folders": [
            {"path": grant["root"], "mode": grant["mode"],
             "project": grant.get("project")}
            for grant in self.permissions.active()
            if grant.get("capability") == "read_folder"]}
        return result
