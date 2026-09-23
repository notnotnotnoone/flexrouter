"""The terminal surface: a Textual TUI, and the rich styling the CLI shares.

Nothing is imported here on purpose. `flexrouter.tui.facts` and
`flexrouter.tui.render` are cheap (stdlib, rich and the rest of flexrouter),
so the ordinary commands can import them without pulling textual in;
`flexrouter.tui.app` is where textual lives, and only `flexrouter tui`
imports it.
"""
