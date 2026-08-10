"""Library-page composition for the server-rendered web surface."""

from __future__ import annotations

from html import escape
import re

from ...models import TextStrataItem
from ..browser_assets import client_asset_tag
from ..components import about_dialog_html, confirm_dialog_html, maintenance_dialog_html, menubar_html, review_dialog_html, settings_dialog_html, settings_open_button_html, sync_dialog_html, toast_container_html, trash_dialog_html
from ..skin import Skin, skin_vars


def _skin_vars(skin: Skin) -> str:
    return skin_vars(skin)


def _excerpt(body: str, max_chars: int = 200) -> str:
    if not body:
        return ""
    text = re.sub(r"(?m)^\s*#{1,6}\s+", "", body)
    text = re.sub(r"[*_`~]", "", text)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = text.strip()
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return ""
    first = lines[0]
    if len(first) > max_chars:
        first = first[:max_chars].rstrip() + "…"
    return first

def render_library_index(
    items: list[TextStrataItem],
    skin: Skin,
    *,
    version: str = "",
    page_title: str | None = None,
    page_meta: str | None = None,
    dashboard_html: str | None = None,
    sidebar_extra_html: str | None = None,
    search_query: str | None = None,
    sort: str | None = None,
    search_reasons: dict | None = None,
    contributor_filter: list[str] | None = None,
    active_tag: str | None = None,
    empty_title: str | None = None,
    empty_message: str | None = None,
) -> str:
    title = page_title or (f"Tag: {active_tag}" if active_tag else "Library")
    count = len(items)
    count_label = f"{count} note{'s' if count != 1 else ''}"
    search_reasons = search_reasons or {}
    selected_contributors = set(contributor_filter or [])

    card_rows = []
    empty_state = ""
    if not items:
        empty_state = (
            '<div class="empty-state"><p>'
            + escape(empty_title or "Your knowledge base is empty")
            + '</p><p class="meta">'
            + escape(empty_message or "Ingest some content to get started.")
            + "</p></div>"
        )
    contributor_labels = {"via_script": "Script", "human": "Human", "via_ai": "AI"}
    for item in items:
        item_type = escape(item.type.value.replace("_", " "))
        item_id = escape(item.id, quote=True)
        title_text = escape(item.title or item.id)
        title_data = escape(item.title or item.id, quote=True).lower()
        excerpt = _excerpt(item.body)
        excerpt_html = f'<p class="entry-excerpt">{escape(excerpt)}</p>' if excerpt else ""
        tags_html = "".join(
            '<a class="tag" href="/tag/' + escape(tag, quote=True) + '">' + escape(tag) + "</a>"
            for tag in item.tags
        ) or '<span class="meta">No tags</span>'
        reasons_html = "".join(
            '<span class="reason-chip">' + escape(reason) + "</span>"
            for reason in search_reasons.get(item.id, [])
        )
        chain = [part.strip() for part in item.provenance.contributor_chain.split(",") if part.strip()]
        contributor_html = "".join(
            '<span class="contributor-chip">' + escape(contributor_labels.get(part, part)) + "</span>"
            for part in chain
        )
        updated = str(item.extra.get("last_edited_at") or item.provenance.ingested_at or "")
        updated_label = updated[:10] if updated else "Unknown date"
        needs_curation = not item.tags or not (
            item.provenance.source_url or item.extra.get("document_date")
        )
        search_data = escape(
            (item.title or "") + " " + item.id + " " + item.type.value + " " + " ".join(item.tags) + " " + (excerpt or ""),
            quote=True,
        ).lower()
        card_rows.append(
            '<article class="entry" id="item-' + item_id + '" data-search="' + search_data
            + '" data-title="' + title_data + '" data-tags="' + escape(" ".join(item.tags), quote=True).lower()
            + '" data-contributors="' + escape(",".join(chain), quote=True)
            + '" data-tag-count="' + str(len(item.tags)) + '" data-updated="' + escape(updated, quote=True)
            + '" data-needs-curation="' + ("true" if needs_curation else "false") + '">'
            + '<div class="entry-main"><h3 class="entry-title"><a href="/item/' + item_id + '">' + title_text + '</a></h3>'
            + excerpt_html
            + '<div class="entry-signals">' + reasons_html + contributor_html + '</div></div>'
            + '<div class="entry-details"><span>' + item_type + '</span><time datetime="' + escape(updated, quote=True) + '">' + escape(updated_label) + '</time><code>' + escape(item.id) + '</code><div class="chips">' + tags_html + '</div></div>'
            + '<div class="entry-actions"><button type="button" class="entry-action" data-revisions="' + item_id + '">History</button><button type="button" class="entry-action entry-action-danger" data-trash-item="' + item_id + '">Trash</button></div>'
            + '</article>'
        )
    cards_html = "\n".join(card_rows)
    page_meta_html = '<p class="search-meta">' + page_meta + "</p>" if page_meta else ""

    contributor_filters = "".join(
        '<label><input type="checkbox" name="contributor" value="' + value + '"'
        + (" checked" if value in selected_contributors else "") + "> " + label + "</label>"
        for value, label in (("via_script", "Script"), ("human", "Human"), ("via_ai", "AI"))
    )
    active_context = (
        '<div class="active-context"><span>Tag</span><strong>' + escape(active_tag) + '</strong><a href="/">Clear</a></div>'
        if active_tag else ""
    )

    open_ingest_icon = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none"'
        + ' stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        + '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>'
    )

    base_css = (
        "*{box-sizing:border-box}"
        + "html,body{margin:0;padding:0;background:var(--bg);color:var(--text);font-family:var(--font-body);font-size:var(--font-scale)}"
        + "body{min-height:100vh;line-height:1.6}"
        + "a{color:var(--accent)}"
        + "a:focus-visible,button:focus-visible,textarea:focus-visible{outline:3px solid var(--accent);outline-offset:3px}"
        + ".skip-link{position:absolute;left:-9999px;top:0}"
        + ".skip-link:focus{left:1rem;top:1rem;z-index:50;background:var(--surface);padding:.5rem .75rem;border:1px solid var(--border)}"
    )

    ui_font = "font-family:var(--font-ui)"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} - TextStrata</title>
  <style>
    :root {{{_skin_vars(skin)}}}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--text); {ui_font}; font-size: var(--font-scale); }}
    body {{ min-height: 100vh; line-height: 1.6; }}
    a {{ color: var(--accent); }}
    a:focus-visible, button:focus-visible, textarea:focus-visible {{ outline: 3px solid var(--accent); outline-offset: 3px; }}
    .skip-link {{ position: absolute; left: -9999px; top: 0; }}
    .skip-link:focus {{ left: 1rem; top: 1rem; z-index: 50; background: var(--surface); padding: .5rem .75rem; border: 1px solid var(--border); }}
    .page {{ width: min(100% - 2rem, var(--max-width)); margin: 0 auto; }}
    .meta {{ color: var(--muted); {ui_font}; font-size: .88rem; }}
    .btn {{ padding: .4rem .7rem; border: 1px solid var(--border); background: var(--surface); color: var(--text); cursor: pointer; font: inherit; font-size: .85rem; {ui_font}; }}
    .btn-primary {{ background: var(--accent); color: white; border-color: var(--accent); }}
    .btn-active {{ background: var(--accent-soft) !important; color: var(--accent) !important; border-color: var(--accent) !important; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: .3rem; }}
    .tag {{ display: inline-flex; align-items: center; padding: .15rem .5rem; background: var(--accent-soft); color: var(--text); {ui_font}; font-size: .82rem; border: 1px solid color-mix(in srgb, var(--accent) 20%, var(--border)); text-decoration: none; }}
    .empty {{ color: var(--muted); font-style: italic; }}
    .site-header {{ display: flex; justify-content: space-between; align-items: center; min-height: 3.4rem; padding: .65rem 0; border-bottom: 1px solid var(--border); font-family: var(--font-ui); }}
    .site-header h1 {{ font-size: 1.3rem; line-height: 1.2; margin: 0; font-weight: 700; }}
    .library-bar {{ display: flex; gap: .6rem; align-items: center; }}
    .search-area {{ position: sticky; top: 1.8rem; z-index: 18; padding: .7rem 0; margin-bottom: .5rem; background: var(--bg); border-bottom: 1px solid var(--border); }}
    .search-form {{ display: grid; grid-template-columns: minmax(16rem, 1fr) auto auto; gap: .5rem; align-items: center; }}
    .search-form input[type="search"] {{ min-width: 0; min-height: 2.65rem; padding: .55rem .75rem; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); font: inherit; font-size: .92rem; }}
    .search-form select {{ min-height: 2.65rem; padding: .4rem .55rem; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); font: inherit; font-size: .82rem; }}
    .filter-bar {{ grid-column: 1 / -1; display: flex; align-items: center; gap: .7rem; min-height: 1.8rem; }}
    .contributor-filters {{ display: flex; gap: .65rem; align-items: center; padding: 0; margin: 0; border: 0; color: var(--muted); font-size: .78rem; }}
    .contributor-filters legend {{ float: left; margin-right: .2rem; font-weight: 600; color: var(--text); }}
    .contributor-filters label {{ display: inline-flex; align-items: center; gap: .2rem; white-space: nowrap; }}
    .active-context {{ display: inline-flex; align-items: center; gap: .4rem; padding: .15rem .45rem; border: 1px solid var(--border); border-radius: 999px; background: var(--surface); font-size: .75rem; }}
    .active-context span {{ color: var(--muted); }}
    .search-meta {{ font-family: var(--font-ui); font-size: .82rem; color: var(--muted); margin: .2rem 0 .75rem; }}
    .empty-state {{ text-align: center; padding: 3rem 1rem; }}
    .empty-state p {{ font-family: var(--font-ui); font-size: 1.05rem; }}
    .menubar {{ display: flex; align-items: center; background: var(--surface); border-bottom: 1px solid var(--border); {ui_font}; font-size: .82rem; user-select: none; min-height: 1.8rem; margin: 0; padding: 0 2rem; position: sticky; top: 0; z-index: 25; }}
    .menubar-left {{ display: flex; align-items: stretch; }}
    .menu-item {{ position: relative; }}
    .menu-trigger {{ background: none; border: none; color: var(--text); font: inherit; padding: .25rem .6rem; cursor: pointer; border-radius: 0; }}
    .menu-dropdown {{ display: none; position: absolute; top: 100%; left: 0; min-width: 13rem; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; box-shadow: 0 8px 24px rgba(0,0,0,.12); z-index: 200; padding: .3rem 0; }}
    .menu-dropdown.open {{ display: block; }}
    .menu-dropdown button {{ display: block; width: 100%; text-align: left; background: none; border: none; color: var(--text); font: inherit; font-size: .82rem; padding: .45rem 1rem; cursor: pointer; }}
    .menu-label {{ display: block; }}
    .menu-desc {{ display: block; margin-top: .12rem; font-size: .72rem; color: var(--muted); line-height: 1.3; white-space: normal; }}
    .menubar-right {{ display: flex; align-items: center; padding: 0 .5rem; gap: .4rem; margin-left: auto; }}
    .version-badge {{ font: 500 .7rem var(--font-ui); color: var(--muted); background: var(--surface-alt); border: 1px solid var(--border); border-radius: 999px; padding: .1rem .5rem; }}
    .about-dialog table {{ width: 100%; border-collapse: collapse; font-size: .85rem; }}
    .about-dialog td {{ padding: .35rem .5rem; border-bottom: 1px solid var(--border); }}
    .about-dialog td:first-child {{ color: var(--muted); white-space: nowrap; width: 30%; }}
    .about-dialog pre {{ background: var(--surface-alt); padding: .6rem .8rem; border-radius: 6px; {ui_font}; font-size: .8rem; }}
    .about-dialog .install-active {{ background: var(--accent-soft); border: 1px solid var(--accent); border-radius: 6px; padding: .6rem .8rem; margin: .5rem 0; }}
    .toast-container {{ position: fixed; bottom: 1.5rem; right: 1.5rem; z-index: 100; display: flex; flex-direction: column; gap: .5rem; pointer-events: none; }}
    .toast {{ padding: .85rem 1.1rem; border-radius: var(--radius); background: var(--surface); border: 1px solid var(--border); box-shadow: 0 8px 28px rgba(0,0,0,.15); {ui_font}; font-size: .88rem; color: var(--text); animation: toast-in 180ms ease forwards; pointer-events: auto; max-width: 26rem; }}
    .toast-success {{ border-left: 4px solid var(--success); }}
    .toast-error {{ border-left: 4px solid var(--danger); }}
    @keyframes toast-in {{ from {{ opacity: 0; transform: translateY(1rem) scale(.96); }} to {{ opacity: 1; transform: translateY(0) scale(1); }} }}
    .hist-table {{ width: 100%; border-collapse: collapse; font-size: .82rem; }}
    .hist-table th, .hist-table td {{ text-align: left; padding: .35rem .5rem; border-bottom: 1px solid var(--border); vertical-align: top; }}
    .dialog-head {{ display: flex; justify-content: space-between; align-items: center; padding: .7rem 1rem; border-bottom: 1px solid var(--border); }}
    .dialog-head h2 {{ margin: 0; font-size: 1rem; font-weight: 700; }}
    .dialog-body {{ padding: .7rem 1rem 1rem; font-size: .9rem; }}
    .kbd {{ display: inline-flex; align-items: center; justify-content: center; min-width: 1.5rem; height: 1.4rem; padding: 0 .3rem; border-radius: 4px; background: var(--surface-alt); border: 1px solid var(--border); {ui_font}; font-size: .72rem; color: var(--muted); }}

    /* --- Workspace navigation --- */
    .sidebar-backdrop {{ display: none; position: fixed; inset: 0; z-index: 19; background: rgba(0,0,0,.3); }}
    body.sidebar-open .sidebar-backdrop {{ display: block; }}
    .sidebar {{ position: fixed; top: 1.8rem; left: -15rem; bottom: 0; width: 15rem; background: var(--surface); border-right: 1px solid var(--border); z-index: 20; transition: left 180ms ease; overflow-y: auto; padding: 0 0 1rem; font-family: var(--font-ui); font-size: .84rem; }}
    body.sidebar-open .sidebar {{ left: 0; }}
    .sidebar-header {{ display: flex; align-items: center; justify-content: space-between; gap: .5rem; min-height: 4.4rem; padding: .75rem .8rem; border-bottom: 1px solid var(--border); }}
    .workspace-brand {{ display: grid; color: var(--text); text-decoration: none; line-height: 1.2; }}
    .workspace-brand strong {{ font-size: .96rem; }}
    .workspace-brand span {{ margin-top: .2rem; color: var(--muted); font-size: .7rem; }}
    #sidebar-toggle {{ padding: .3rem .45rem; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); font: inherit; font-size: .74rem; cursor: pointer; }}
    .sidebar-inner {{ padding: .7rem .65rem 0; }}
    .workspace-nav {{ display: grid; gap: .12rem; margin-bottom: .8rem; }}
    .workspace-link {{ display: grid; width: 100%; min-height: 2.55rem; padding: .4rem .55rem; border: 0; border-radius: 6px; background: transparent; color: var(--text); text-align: left; text-decoration: none; font: inherit; cursor: pointer; }}
    .workspace-link strong {{ font-size: .83rem; font-weight: 600; }}
    .workspace-link span {{ color: var(--muted); font-size: .69rem; line-height: 1.2; }}
    .workspace-link:hover, .workspace-link[aria-current="page"] {{ background: var(--accent-soft); color: var(--text); }}
    .workspace-link[aria-current="page"] {{ box-shadow: inset 3px 0 0 var(--accent); }}
    .workspace-separator {{ height: 1px; margin: .4rem .55rem; background: var(--border); }}
    .sidebar-filter-label {{ display: block; margin: .2rem 0 .25rem; color: var(--muted); font-size: .72rem; font-weight: 600; }}
    .sidebar-inner input[type="search"] {{ width: 100%; min-height: 2.3rem; padding: .4rem .55rem; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-alt); color: var(--text); font: inherit; font-size: .8rem; margin-bottom: .8rem; }}
    .sidebar-section, .sidebar-module {{ margin-top: .8rem; padding-top: .7rem; border-top: 1px solid var(--border); }}
    .sidebar-section h2, .sidebar-module h2 {{ font-size: .7rem; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin: 0 0 .35rem; font-weight: 700; }}
    .sidebar-section ul {{ list-style: none; padding: 0; margin: 0; }}
    .sidebar-section li {{ padding: .2rem 0; }}
    .sidebar-section a, .sidebar-module a {{ color: var(--text); text-decoration: none; display: grid; padding: .24rem 0; overflow: hidden; text-overflow: ellipsis; }}
    .sidebar-section a:hover, .sidebar-module a:hover {{ color: var(--accent); }}
    .sidebar-module nav {{ display: grid; }}
    .sidebar-module a span {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .sidebar-section a small, .sidebar-module a small {{ font-size: .68rem; color: var(--muted); }}

    /* --- Workspace overview --- */
    .dashboard-grid[hidden] {{ display: none !important; }}
    .dashboard-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0 0 1rem; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }}
    .dash-card {{ min-width: 0; padding: .7rem .85rem; border-right: 1px solid var(--border); font-family: var(--font-ui); font-size: .8rem; }}
    .dash-card:last-child {{ border-right: 0; }}
    .dash-card h2 {{ font-size: .7rem; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin: 0 0 .35rem; }}
    .dash-card ul {{ list-style: none; padding: 0; margin: 0; }}
    .dash-card li {{ padding: .12rem 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}

    /* --- Dense knowledge rows --- */
    #knowledge-grid {{ padding-bottom: 2rem; border-top: 1px solid var(--border); }}
    .entry {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(9rem, 13rem) auto; gap: 1rem; align-items: start; min-height: 5.1rem; padding: .65rem .35rem; border-bottom: 1px solid var(--border); }}
    .entry:hover {{ background: color-mix(in srgb, var(--surface) 72%, transparent); }}
    .entry:target {{ background: var(--accent-soft); }}
    .entry-main {{ min-width: 0; }}
    .entry-title {{ margin: 0; font-size: .96rem; line-height: 1.3; font-weight: 650; font-family: var(--font-ui); }}
    .entry-title a {{ text-decoration: none; color: var(--text); }}
    .entry-title a:hover {{ color: var(--accent); text-decoration: underline; }}
    .entry-excerpt {{ margin: .18rem 0 0; color: var(--muted); font-size: .81rem; line-height: 1.4; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }}
    .entry-signals {{ display: flex; gap: .3rem; flex-wrap: wrap; min-height: 1rem; margin-top: .35rem; }}
    .reason-chip, .contributor-chip {{ display: inline-flex; padding: .08rem .35rem; border-radius: 999px; font-size: .67rem; line-height: 1.35; }}
    .reason-chip {{ background: var(--accent-soft); color: var(--text); }}
    .contributor-chip {{ border: 1px solid var(--border); color: var(--muted); }}
    .entry-details {{ display: grid; gap: .13rem; min-width: 0; color: var(--muted); font-size: .71rem; }}
    .entry-details code {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text); font-size: .69rem; }}
    .entry-details .chips {{ gap: .2rem; margin-top: .18rem; }}
    .entry-details .tag {{ padding: .05rem .3rem; border: 0; border-radius: 999px; font-size: .65rem; }}
    .entry-actions {{ display: grid; gap: .25rem; width: 4.3rem; }}
    .entry-action {{ min-height: 1.8rem; padding: .18rem .35rem; border: 1px solid var(--border); border-radius: 5px; background: var(--surface); color: var(--muted); cursor: pointer; font: .7rem var(--font-ui); }}
    .entry-action:hover {{ color: var(--accent); border-color: var(--accent); }}
    .entry-action-danger:hover {{ color: var(--danger); border-color: var(--danger); }}
    .entry[hidden] {{ display: none; }}
    #filter-empty {{ text-align: center; padding: 2rem 1rem; color: var(--muted); font-family: var(--font-ui); }}
    #filter-empty[hidden] {{ display: none; }}

    /* --- Focus mode --- */
    body.focus-mode .sidebar {{ left: -16rem !important; }}
    body.focus-mode .sidebar-backdrop {{ display: none !important; }}
    body.focus-mode #sidebar-toggle {{ display: none; }}

    dialog {{ width: min(44rem, calc(100% - 2rem)); max-height: calc(100vh - 2rem); overflow: auto; padding: 0; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); color: var(--text); }}
    dialog::backdrop {{ background: rgba(0,0,0,.45); }}
    .settings-section {{ padding: .7rem 0; border-bottom: 1px solid var(--border); }}
    .settings-section:last-child {{ border-bottom: none; }}
    .settings-section h3 {{ margin: 0 0 .5rem; font-size: .9rem; }}
    .settings-grid {{ display: grid; grid-template-columns: auto minmax(8rem, 1fr); gap: .5rem .8rem; align-items: center; }}
    .settings-grid select, .settings-grid input {{ padding: .35rem .5rem; border: 1px solid var(--border); background: var(--bg); color: var(--text); font: inherit; }}
    .settings-actions {{ display: flex; gap: .5rem; flex-wrap: wrap; margin-top: .6rem; }}
    .trash-row, .revision-row {{ display: flex; justify-content: space-between; gap: .5rem; align-items: center; padding: .35rem 0; border-bottom: 1px solid var(--border); }}
    .job {{ padding: .65rem 0; border-bottom: 1px solid var(--border); }}
    .job:last-child {{ border-bottom: none; }}
    .actions {{ display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; margin-top: .4rem; }}
    .danger-text {{ color: var(--danger); }}
    .sr-only {{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }}
    .review-row {{ padding: .85rem 0; border-bottom: 1px solid var(--border); }}
    .review-row:last-child {{ border-bottom: none; }}
    .review-row-head {{ display: flex; justify-content: space-between; align-items: baseline; gap: .75rem; }}
    .review-context {{ margin: .55rem 0; padding: .55rem .7rem; background: var(--surface-alt); border-left: 3px solid var(--border); white-space: pre-wrap; }}
    .review-tags {{ display: grid; grid-template-columns: 7rem minmax(0, 1fr); gap: .3rem .7rem; margin: .5rem 0; font-size: .85rem; }}
    .review-tags dt {{ color: var(--muted); }}
    .review-tags dd {{ margin: 0; }}
    .shortcuts-help {{ position: fixed; bottom: 1.5rem; right: 1.5rem; z-index: 80; min-width: 18rem; padding: 1rem; background: var(--surface); border: 1px solid var(--border); box-shadow: 0 8px 32px rgba(0,0,0,.18); }}
    .shortcuts-help h3 {{ margin: 0 0 .5rem; }}
    .shortcuts-help table {{ width: 100%; }}

    @media (min-width: 960px) {{
      .sidebar {{ left: 0; }}
      .sidebar-backdrop {{ display: none !important; }}
      #sidebar-toggle, #sidebar-open-btn {{ display: none; }}
      .page {{ width: calc(100% - 15rem); max-width: var(--max-width); margin: 0 auto 0 15rem; padding: 0 1.25rem; }}
      body.focus-mode .page {{ width: min(100% - 2rem, var(--max-width)); margin-left: auto; margin-right: auto; }}
    }}
    @media (max-width: 959px) {{
      .dashboard-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .dash-card:nth-child(2) {{ border-right: 0; }}
      .dash-card:nth-child(-n+2) {{ border-bottom: 1px solid var(--border); }}
    }}
    @media (max-width: 700px) {{
      .page {{ width: min(100% - 1rem, var(--max-width)); }}
      .site-header {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: start; gap: .5rem; }}
      .library-bar {{ gap: .35rem; }}
      .library-bar .meta {{ display: none; }}
      .library-bar .btn {{ white-space: nowrap; }}
      .search-form {{ grid-template-columns: minmax(0, 1fr) auto; }}
      .search-form input[type="search"], .filter-bar {{ grid-column: 1 / -1; }}
      .contributor-filters {{ flex-wrap: wrap; }}
      .sidebar {{ width: min(18rem, 88vw); left: -19rem; }}
      .entry {{ grid-template-columns: minmax(0, 1fr) auto; gap: .65rem; }}
      .entry-details {{ grid-column: 1; grid-row: 2; }}
      .entry-actions {{ grid-column: 2; grid-row: 1 / span 2; }}
      .dashboard-grid {{ grid-template-columns: 1fr; }}
      .dash-card {{ border-right: 0; border-bottom: 1px solid var(--border); }}
      .dash-card:last-child {{ border-bottom: 0; }}
    }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ scroll-behavior: auto; transition: none !important; animation: none !important; }} }}
  </style>
</head>
<body>
{menubar_html(version=version)}
  {settings_open_button_html()}
  {about_dialog_html()}
  {settings_dialog_html()}
  {review_dialog_html()}
  {trash_dialog_html()}
  {maintenance_dialog_html()}
  {confirm_dialog_html()}
  <div id="sidebar-backdrop" class="sidebar-backdrop"></div>
  <aside id="sidebar" class="sidebar" aria-label="Workspace navigation">
    <div class="sidebar-header">
      <a class="workspace-brand" href="/"><strong>TextStrata</strong><span>Local knowledge workspace</span></a>
      <button id="sidebar-toggle" type="button" aria-expanded="false" aria-label="Close navigation">Close</button>
    </div>
    <div class="sidebar-inner">
      <nav class="workspace-nav" aria-label="Primary">
        <a class="workspace-link" data-workspace-view="library" href="/"><strong>Library</strong><span>Browse the full corpus</span></a>
        <a class="workspace-link" data-workspace-view="search" href="/search"><strong>Search</strong><span>Find titles, tags, and text</span></a>
        <a class="workspace-link" data-workspace-view="recent" href="/recent"><strong>Recent</strong><span>Latest changed notes</span></a>
        <a class="workspace-link" data-workspace-view="needs-curation" href="/needs-curation"><strong>Needs curation</strong><span>Missing tags or source context</span></a>
        <a class="workspace-link" data-workspace-view="untagged" href="/untagged"><strong>Untagged</strong><span>Notes missing classification</span></a>
        <a class="workspace-link" data-workspace-view="orphaned" href="/orphaned"><strong>Orphaned</strong><span>No cross-links to other notes</span></a>
        <div class="workspace-separator"></div>
        <button class="workspace-link" type="button" data-action="sync"><strong>Imports</strong><span>Queue and acquisition history</span></button>
        <button class="workspace-link" type="button" data-action="review-queue"><strong>Review</strong><span>Resolve suggested changes</span></button>
        <button class="workspace-link" type="button" data-action="trash"><strong>Trash</strong><span>Restore deleted notes</span></button>
      </nav>
      <label class="sidebar-filter-label" for="sidebar-query-library">Filter current list</label>
      <input id="sidebar-query-library" type="search" placeholder="Filter visible notes" aria-label="Filter current note list">
      <div id="sidebar-links">{sidebar_extra_html or ''}</div>
    </div>
  </aside>
  <a class="skip-link" href="#content">Skip to content</a>
  <div class="page">
    <header class="site-header">
      <h1 id="collection-title">{escape(title)}</h1>
      <div class="library-bar">
        <button id="sidebar-open-btn" class="btn" type="button" aria-label="Open navigation">Navigation</button>
        <span class="meta" id="visible-count">{count_label}</span>
        <a id="new-note-link" class="btn btn-primary" href="/new">New note</a>
      </div>
    </header>
    <div class="search-area">
      <form class="search-form" action="/search" method="get" role="search">
        <label class="sr-only" for="query">Search knowledge</label>
        <input id="query" name="q" type="search" placeholder="Search titles, tags, IDs, and full text" value="{escape(search_query or '')}">
        <select name="sort" aria-label="Sort results">
          <option value="relevance"{" selected" if sort=="relevance" or not sort else ""}>Relevance</option>
          <option value="score"{" selected" if sort=="score" else ""}>Importance</option>
          <option value="newest"{" selected" if sort=="newest" else ""}>Newest</option>
          <option value="oldest"{" selected" if sort=="oldest" else ""}>Oldest</option>
        </select>
        <button class="btn" type="submit">Search</button>
        <div class="filter-bar">{active_context}<fieldset class="contributor-filters"><legend>Contributors</legend>{contributor_filters}</fieldset></div>
      </form>
    </div>
    {page_meta_html}
    {dashboard_html or ""}
    <div id="content">
      <div id="knowledge-grid">
        {cards_html}
      </div>
      {empty_state}
      <p id="filter-empty" hidden>No matching notes.</p>
    </div>
  </div>
  {toast_container_html()}
  {sync_dialog_html()}
  <div id="shortcuts-help" class="shortcuts-help" hidden><h3>Keyboard shortcuts</h3><table><tr><td>Search</td><td><span class="kbd">/</span></td></tr><tr><td>New note</td><td><span class="kbd">N</span></td></tr><tr><td>Toggle sidebar</td><td><span class="kbd">B</span></td></tr><tr><td>Help</td><td><span class="kbd">?</span></td></tr></table></div>
{client_asset_tag("library", version)}
</body>
</html>"""
