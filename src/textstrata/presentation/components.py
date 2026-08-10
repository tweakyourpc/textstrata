"""Shared DOM fragments used by multiple presentation surfaces."""

from __future__ import annotations

from html import escape


def _menu_button(action: str, label: str, description: str | None = None) -> str:
    if description:
        return (
            f'<button role="menuitem" data-action="{escape(action, quote=True)}">'
            f'<span class="menu-label">{escape(label)}</span>'
            f'<span class="menu-desc">{escape(description)}</span>'
            '</button>'
        )
    return f'<button role="menuitem" data-action="{escape(action, quote=True)}">{escape(label)}</button>'


def menubar_html(*, version: str = "") -> str:
    view_items = "".join([
        _menu_button("focus-mode", "Focus mode", "Hide side chrome and keep the main reading area."),
        _menu_button("graph", "Knowledge graph", "Open the relationship map for notes and links."),
        _menu_button("media", "Media library", "Browse uploaded photos and copy embeds into notes."),
        '<hr>',
        _menu_button("shortcuts", "Keyboard shortcuts", "Show the available page-level shortcuts."),
    ])
    tool_items = "".join([
        _menu_button("review-queue", "Review queue", "Evaluate suggested tags with note context before applying them."),
        _menu_button("trash", "Trash", "Restore notes or permanently remove trashed content."),
        '<hr>',
        _menu_button("sync", "Open import history", "Review queued and completed ingestion jobs."),
        '<hr>',
        _menu_button("maintenance", "Maintenance", "Retention, service restart, and channel purge operations."),
    ])
    return f"""<nav class="menubar" role="menubar">
    <div class="menubar-left">
      <div class="menu-item"><button class="menu-trigger" data-menu="file-menu">File</button><div class="menu-dropdown" id="file-menu" role="menu">{_menu_button("new-note", "New note")}{_menu_button("setup", "Setup & capabilities")}{_menu_button("settings", "Settings")}</div></div>
      <div class="menu-item"><button class="menu-trigger" data-menu="view-menu">View</button><div class="menu-dropdown" id="view-menu" role="menu">{view_items}</div></div>
      <div class="menu-item"><button class="menu-trigger" data-menu="tools-menu">Tools</button><div class="menu-dropdown" id="tools-menu" role="menu">{tool_items}</div></div>
      <div class="menu-item"><button class="menu-trigger" data-menu="help-menu">Help</button><div class="menu-dropdown" id="help-menu" role="menu">{_menu_button("about", "About")}{_menu_button("system-info", "System info")}<hr>{_menu_button("shortcuts", "Keyboard shortcuts")}{_menu_button("docs", "Documentation")}</div></div>
    </div>
    <div class="menubar-right">{'<span class="version-badge" data-version-badge data-version-source="/whoami">' + escape(version) + '</span>' if version else ''}</div>
  </nav><script>
(async()=>{{try{{const response=await fetch('/whoami',{{cache:'no-store'}});if(!response.ok)return;const identity=await response.json();document.querySelectorAll('[data-version-badge]').forEach(node=>node.textContent=identity.version||node.textContent)}}catch{{}}}})();
</script>"""


def about_dialog_html() -> str:
    return """<dialog id="about-dialog" class="about-dialog"><div class="dialog-head"><div><h2 id="about-title">About</h2></div><button type="button" data-close="about-dialog">Close</button></div><div class="dialog-body"><div id="about-content"><p class="meta">Loading...</p></div></div></dialog>"""


def settings_open_button_html() -> str:
    return '<button id="settings-open" type="button" hidden>Open settings</button>'


def settings_dialog_html() -> str:
    return """<dialog id="settings-dialog" aria-labelledby="settings-heading">
    <div class="dialog-head"><div><h2 id="settings-heading">Settings</h2><p class="meta">Appearance and library preferences.</p></div><button type="button" data-close="settings-dialog">Close</button></div>
    <div class="dialog-body">
      <div class="settings-section">
        <h3>Appearance</h3>
        <div class="settings-grid">
          <label for="design-skin">Skin</label><select id="design-skin"><option value="paper">Paper</option><option value="console">Console</option><option value="wiki">Wiki</option></select>
          <label for="design-accent">Accent</label><select id="design-accent"><option value="teal">Teal</option><option value="blue">Blue</option><option value="plum">Plum</option><option value="amber">Amber</option></select>
          <label for="design-density">Density</label><select id="design-density"><option value="comfortable">Comfortable</option><option value="compact">Compact</option><option value="spacious">Spacious</option></select>
          <label for="design-width">Width</label><select id="design-width"><option value="wide">Wide</option><option value="focused">Focused</option><option value="fluid">Fluid</option></select>
          <label for="design-cards">Cards</label><select id="design-cards"><option value="soft">Soft</option><option value="flat">Flat</option><option value="bordered">Bordered</option></select>
          <label for="design-scale">Font scale</label><div><input id="design-scale" type="range" min="90" max="120" value="100"><output id="design-scale-output">100%</output></div>
          <label for="design-motion">Motion</label><label><input id="design-motion" type="checkbox"> Reduced</label>
        </div>
        <div class="settings-actions"><button id="design-save" type="button">Save appearance</button></div>
      </div>
      <div class="settings-section">
        <h3>Library</h3>
        <div class="settings-grid"><label for="revision-limit">Revision limit</label><input id="revision-limit" type="number" min="1" max="50" step="1" value="10"></div>
        <div class="settings-actions"><button id="settings-save" type="button">Save settings</button></div>
      </div>
      <div id="settings-status" class="meta" role="status" aria-live="polite"></div>
    </div>
  </dialog>"""


def review_dialog_html() -> str:
    return """<dialog id="review-dialog" aria-labelledby="review-heading">
    <div class="dialog-head"><div><h2 id="review-heading">Review queue</h2><p class="meta">Apply or reject suggestions after checking the note context.</p></div><button type="button" data-close="review-dialog">Close</button></div>
    <div class="dialog-body">
      <div id="review-list" aria-live="polite"><p class="meta">Loading...</p><span class="sr-only">Current tags. Suggested tags. Apply suggested tags. Reject suggestion. openTarget === "review"</span></div>
      <div id="review-status" class="meta" role="status" aria-live="polite"></div>
    </div>
  </dialog>"""


def sync_dialog_html() -> str:
    return """<dialog id="sync-dialog" aria-labelledby="sync-heading"><div class="dialog-head"><div><h2 id="sync-heading">Import history</h2><p class="meta">Inspect, stop, or remove acquisition jobs.</p></div><button type="button" data-close="sync-dialog">Close</button></div><div class="dialog-body"><div class="settings-actions"><button id="queue-refresh" type="button">Refresh</button><button id="queue-clear" type="button">Clear finished jobs</button></div><div id="sync-status" aria-live="polite"><p class="meta">Loading...</p></div></div></dialog>"""


def trash_dialog_html() -> str:
    return """<dialog id="trash-dialog" aria-labelledby="trash-heading"><div class="dialog-head"><div><h2 id="trash-heading">Trash</h2><p class="meta">Restore notes or permanently delete them.</p></div><button type="button" data-close="trash-dialog">Close</button></div><div class="dialog-body"><div id="textstrata-trash" aria-live="polite"><p class="meta">Loading...</p></div><div class="settings-actions"><button id="textstrata-trash-empty" type="button" class="btn btn-danger">Empty trash</button></div><div id="trash-status" class="meta" role="status" aria-live="polite"></div></div></dialog>"""


def maintenance_dialog_html() -> str:
    return """<dialog id="maintenance-dialog" aria-labelledby="maintenance-heading"><div class="dialog-head"><div><h2 id="maintenance-heading">Maintenance</h2><p class="meta">Manage retention and service operations.</p></div><button type="button" data-close="maintenance-dialog">Close</button></div><div class="dialog-body"><div class="settings-section"><h3>Retained originals</h3><div class="settings-grid"><label for="retain-default">Retain by default</label><label><input id="retain-default" type="checkbox"> Enabled</label><label for="retention-mode">Retention mode</label><select id="retention-mode"><option value="days">Days</option><option value="never">Keep forever</option></select><label for="retention-days">Retention days</label><input id="retention-days" type="number" min="1" max="3650" step="1" value="30"></div><div class="settings-actions"><button id="retention-save" type="button">Save retention</button></div></div><div class="settings-section"><h3>Services</h3><div class="settings-actions"><button id="restart-engine" type="button">Recheck ingestion engine</button><button id="restart-server" type="button">Restart server</button></div></div><div class="settings-section"><h3>Channel purge</h3><div class="settings-actions"><label for="purge-channel" class="sr-only">Channel slug</label><input id="purge-channel" type="text" placeholder="Channel slug"><button id="channel-purge" type="button" class="btn btn-danger">Purge channel</button></div></div><pre id="path-readout" class="meta"></pre><div id="maintenance-status" class="meta" role="status" aria-live="polite"></div></div></dialog>"""


def confirm_dialog_html() -> str:
    return """<dialog id="confirm-dialog">
    <div class="dialog-head"><div><h2 id="confirm-title">Confirm action</h2></div><button type="button" data-close="confirm-dialog">Close</button></div>
    <div class="dialog-body">
      <p id="confirm-warning"></p>
      <p class="meta"><a id="confirm-reference" href="/item/system.operations-error-reference">Operation notes</a></p>
      <div class="settings-actions"><button id="confirm-cancel" type="button">Cancel</button><button id="confirm-accept" type="button" class="btn btn-danger">Proceed</button></div>
    </div>
  </dialog>"""


def toast_container_html() -> str:
    return '<div id="toast-container" class="toast-container"></div>'
