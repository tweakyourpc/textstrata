"""Item-page composition for the server-rendered web surface."""

from __future__ import annotations

import json
from html import escape

from ...captions import has_timestamped_transcript
from ..client_scripts import item_page_script
from ..components import about_dialog_html, menubar_html, toast_container_html
from ..markdown import calculate_read_time, format_dt, generate_toc, markdown_to_html
from ...render_context import RenderContext
from ..skin import PAPER_SKIN, Skin, skin_vars


def _skin_vars(skin: Skin) -> str:
    return skin_vars(skin)


def render_item_html(ctx: RenderContext, skin: Skin = PAPER_SKIN, *, version: str = "") -> str:
    item = ctx.item
    known = set(ctx.known_ids)

    def _tag_link(tag: str) -> str:
        return (
            f'<span class="tag-chip"><a class="tag" href="/tag/{escape(tag, quote=True)}">{escape(tag)}</a>'
            f'<button type="button" class="tag-remove" data-remove-tag="{escape(tag, quote=True)}" '
            f'title="Remove tag {escape(tag, quote=True)}" aria-label="Remove tag {escape(tag, quote=True)}">&times;</button></span>'
        )

    def _id_link(item_id: str, *, soft: bool = False) -> str:
        cls = "xref xref-soft" if soft else "xref"
        if item_id in known:
            return f'<a class="{cls}" href="/item/{escape(item_id, quote=True)}"><code>{escape(item_id)}</code></a>'
        # Unresolved reference: show it but make clear it isn't in the textstrata.
        return (
            f'<code class="xref-missing" title="not in textstrata">{escape(item_id)}</code>'
        )

    tags = (
        "".join(_tag_link(tag) for tag in item.tags)
        or '<span class="empty">No tags</span>'
    )
    link_resolver = {
        other_id.casefold(): (other_id, title)
        for other_id, title in ctx.item_titles.items()
    }
    for alias, other_id in ctx.item_aliases.items():
        link_resolver.setdefault(alias.casefold(), (other_id, ctx.item_titles.get(other_id, other_id)))
    html_body = markdown_to_html(item.body, item.provenance.source_url, link_resolver=link_resolver)
    aliases_json = escape(json.dumps(item.aliases), quote=True)
    escaped_raw = escape(ctx.raw_markdown or "") if ctx.raw_markdown else ""
    toc_html = generate_toc(item.body)
    original_date = format_dt(str(item.extra.get("document_date") or item.extra.get("source_date") or item.extra.get("published_at") or ""))
    ingested_date = format_dt(item.provenance.ingested_at)
    last_edited_date = format_dt(str(item.extra.get("last_edited_at") or ""))
    read_time_minutes = calculate_read_time(ctx.raw_markdown if ctx.raw_markdown is not None else item.body)
    infobox_rows = "".join(
        f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>"
        for k, v in (
            ("Type", item.type.value.replace("_", " ")),
            ("ID", item.id),
            ("Aliases", ", ".join(item.aliases) or "-"),
            ("Author", item.provenance.authorship or "-"),
            ("AI model", " / ".join(filter(None, (item.provenance.ai_vendor, item.provenance.ai_model))) or "-"),
            ("Original date", original_date or "-"),
            ("Ingested", ingested_date or "-"),
            ("Last edited", last_edited_date or "-"),
            ("Priority", str(item.retrieval_priority)),
            ("Contributors", (item.provenance.contributor_chain or "-").replace(",", " +")),
        )
    )
    related_list = (
        "".join(f"<li>{_id_link(rel)}</li>" for rel in item.related)
        or '<li class="empty">None</li>'
    )
    deps_list = (
        "".join(f"<li>{_id_link(dep)}</li>" for dep in item.dependencies)
        or '<li class="empty">None</li>'
    )
    def _title_link(item_id: str) -> str:
        """Link showing the item title (falls back to ID). Used in similar/backlinks panels."""
        display = escape(ctx.item_titles.get(item_id, item_id))
        if item_id in known:
            return f'<a class="panel-link" href="/item/{escape(item_id, quote=True)}">{display}</a>'
        return f'<span class="panel-link xref-missing" title="not in textstrata">{display}</span>'

    def _why_label(item_id: str) -> str:
        raw = ctx.why_related.get(item_id, "")
        if not raw:
            return ""
        # Trim verbose "shared terms: a, b, c" -> "a · b · c"
        if raw.startswith("shared terms: "):
            parts = raw.removeprefix("shared terms: ").split(", ")
            raw = " · ".join(parts[:3])
        elif raw.startswith("similar content: "):
            parts = raw.removeprefix("similar content: ").split(", ")
            raw = " · ".join(parts[:3])
        return f'<div class="why-label">{escape(raw)}</div>'

    similar_list = (
        "".join(
            f"<li>{_title_link(similar_id)}{_why_label(similar_id)}</li>"
            for similar_id in ctx.similar_ids
            if similar_id != item.id
        )
        or '<li class="empty">None</li>'
    )
    backlinks_list = (
        "".join(
            f"<li>{_title_link(getattr(link, 'source', ''))}{_why_label(getattr(link, 'source', ''))}</li>"
            for link in ctx.incoming_links
        )
        or '<li class="empty">None</li>'
    )
    tag_list = tags
    has_toc = toc_html is not None
    score_badge = (
        f' <span class="ks-badge" title="Knowledge score">⬡ {int(round(ctx.knowledge_score))}</span>'
        if ctx.knowledge_score is not None
        else ""
    )
    read_time_badge = f' <span class="readtime-badge" title="Estimated reading time">&#x23F1;&#xFE0F; {read_time_minutes} min read</span>' if read_time_minutes else ""
    contributor_badge = ""
    chain = item.provenance.contributor_chain.strip()
    if chain:
        labels = {"via_script": "script", "human": "human", "via_ai": "AI"}
        parts = [labels.get(c, c) for c in (p.strip() for p in chain.split(",")) if c]
        contributor_badge = (
            '<span class="contrib-badge" title="Contributor chain">'
            + " + ".join(parts)
            + "</span>"
        )
    caption_export_html = ""
    if has_timestamped_transcript(item):
        item_id = escape(item.id, quote=True)
        caption_export_html = f"""
        <details class="caption-export">
          <summary class="btn">Export captions</summary>
          <div class="caption-export-menu">
            <a href="/api/notes/{item_id}/export/vtt" download>Download .VTT</a>
            <a href="/api/notes/{item_id}/export/srt" download>Download .SRT</a>
          </div>
        </details>"""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(ctx.title)}</title>
  <style>
    :root {{{_skin_vars(skin)}}}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--text); font-family: var(--font-body); font-size: var(--font-scale); }}
    body {{ min-height: 100vh; line-height: 1.6; }}
    a {{ color: var(--accent); }}
    a:focus-visible, button:focus-visible, textarea:focus-visible {{ outline: 3px solid var(--accent); outline-offset: 3px; }}
    .skip-link {{ position: absolute; left: -9999px; top: 0; }}
    .skip-link:focus {{ left: 1rem; top: 1rem; z-index: 50; background: var(--surface); padding: 0.5rem 0.75rem; border: 1px solid var(--border); }}
    .page {{ width: min(100% - 2rem, var(--max-width)); margin: 0 auto; }}
    .site-header {{ display: flex; justify-content: space-between; align-items: center; padding: .5rem 0; border-bottom: 1px solid var(--border); margin-bottom: 1rem; font-family: var(--font-ui); }}
    .site-nav {{ display: flex; gap: .6rem; align-items: center; }}
    .wiki-suggestions {{ position: absolute; left: 0; right: 0; top: calc(100% - .4rem); z-index: 40; display: none; max-height: 14rem; overflow-y: auto; min-width: 18rem; background: var(--surface); border: 1px solid var(--border); box-shadow: var(--card-shadow); }}
    .wiki-suggestions.open {{ display: block; }}
    .wiki-suggestion {{ display: block; width: 100%; padding: .45rem .65rem; border: 0; background: var(--surface); color: var(--text); text-align: left; cursor: pointer; font: .82rem var(--font-ui); }}
    .wiki-suggestion:hover, .wiki-suggestion[aria-selected="true"] {{ background: var(--accent-soft); }}
    .alias-editor {{ margin: .7rem 0; padding: .65rem .75rem; border: 1px solid var(--border); background: var(--surface-alt); font-family: var(--font-ui); }}
    .alias-editor form {{ display: flex; gap: .4rem; margin-top: .4rem; }}
    .alias-chip {{ display: inline-flex; align-items: center; gap: .25rem; margin: .15rem .25rem .15rem 0; padding: .15rem .4rem; border: 1px solid var(--border); border-radius: 999px; background: var(--surface); font-size: .78rem; }}
    .editor-textarea-wrap {{ position: relative; }}
    .back-link {{ font-size: .9rem; }}
    .title {{ font-family: var(--font-ui); font-size: clamp(1.6rem, 3.2vw, 2.4rem); line-height: 1.2; border-bottom: 0; margin: 0 0 .1rem; }}
    .byline {{ color: var(--muted); font-family: var(--font-ui); font-size: .85rem; margin: 0 0 .6rem; }}
    .layout {{ display: grid; grid-template-columns: 16rem minmax(0, 1fr) 17rem; gap: 1.5rem; padding-bottom: 2rem; }}
    .side-panel {{ font-family: var(--font-ui); font-size: .88rem; }}
    .toc {{ background: var(--surface); border: 1px solid var(--border); padding: .6rem .8rem; font-size: .88rem; }}
    .toc-head {{ font-weight: 700; padding-bottom: .3rem; border-bottom: 1px solid var(--border); margin-bottom: .3rem; text-transform: uppercase; font-size: .78rem; letter-spacing: .04em; color: var(--muted); }}
    .sidebar-search {{ display:grid; gap:.35rem; margin-bottom:.8rem; }} .sidebar-search input {{ width:100%; min-height:2.5rem; padding:.55rem .7rem; border:1px solid var(--border); border-radius:10px; background:var(--surface); color:var(--text); font:inherit; }}
    .toc ul {{ list-style: none; padding: 0; margin: 0; }}
    .toc li {{ padding: .15rem 0; }}
    .toc a {{ color: var(--text); text-decoration: none; }}
    .toc a:hover {{ text-decoration: underline; }}
    .infobox {{ background: var(--surface); border: 1px solid var(--border); font-size: .85rem; margin-bottom: 1rem; }}
    .infobox-title {{ background: color-mix(in srgb, var(--accent) 10%, var(--surface)); padding: .5rem .7rem; font-weight: 700; font-size: .9rem; border-bottom: 1px solid var(--border); font-family: var(--font-ui); }}
    .infobox table {{ width: 100%; border-collapse: collapse; }}
    .infobox th, .infobox td {{ padding: .35rem .7rem; text-align: left; vertical-align: top; border-bottom: 1px solid color-mix(in srgb, var(--border) 40%, transparent); font-size: .85rem; }}
    .infobox th {{ width: 35%; color: var(--muted); font-weight: 600; white-space: nowrap; }}
    .infobox td {{ width: 65%; }}
    .infobox .tag-row td {{ padding: .35rem .7rem .5rem; border-bottom: none; }}
    .infobox .tag-row .chips {{ gap: .25rem; }}
    .infobox .tag-row .tag {{ font-size: .78rem; padding: .1rem .4rem; }}
    .article {{ background: var(--surface); padding: .5rem 1.5rem 1.5rem; min-width: 0; }}
    .body :is(h2,h3,h4) {{ font-family: var(--font-ui); margin: 1.25rem 0 .5rem; }}
    .body h2 {{ font-size: 1.3rem; border-bottom: 1px solid var(--border); padding-bottom: .15rem; }}
    .body h3 {{ font-size: 1.1rem; }}
    .body p, .body ul, .body pre {{ margin: .5rem 0; line-height: 1.65; }}
    .body pre {{ overflow-x: auto; padding: .8rem 1rem; background: #f6f8fa; color: #24292e; font-family: var(--font-mono); font-size: .85rem; }}
    .body code {{ font-family: var(--font-mono); background: rgba(127,127,127,0.08); padding: .08rem .3rem; font-size: .88em; }}
    .body pre code {{ background: transparent; padding: 0; }}
    .body ul {{ padding-left: 1.6rem; }}
    .body li {{ margin: .15rem 0; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: .3rem; }}
    .tag {{ display: inline-flex; align-items: center; padding: .15rem .5rem; background: var(--accent-soft); color: var(--text); font-family: var(--font-ui); font-size: .82rem; border: 1px solid color-mix(in srgb, var(--accent) 20%, var(--border)); text-decoration: none; }}
    a.tag:hover {{ border-color: var(--accent); text-decoration: none; }}
    .tag-chip {{ display: inline-flex; align-items: stretch; }}
    .tag-chip .tag {{ border-right: 0; }}
    .tag-remove {{ display: inline-flex; align-items: center; justify-content: center; width: 1.3rem; background: var(--accent-soft); color: var(--muted); border: 1px solid color-mix(in srgb, var(--accent) 20%, var(--border)); border-left: 1px solid color-mix(in srgb, var(--accent) 35%, var(--border)); cursor: pointer; font-size: .85rem; line-height: 1; padding: 0; }}
    .tag-remove:hover {{ background: var(--danger); color: white; border-color: var(--danger); }}
    .empty {{ color: var(--muted); font-style: italic; }}
    .meta {{ color: var(--muted); font-family: var(--font-ui); font-size: .88rem; }}
    .xref {{ text-decoration: none; font-family: var(--font-mono); font-size: .88rem; }}
    .xref code {{ text-decoration: underline; text-decoration-color: color-mix(in srgb, var(--accent) 50%, transparent); }}
    .xref:hover code {{ text-decoration-color: var(--accent); }}
    .xref-missing {{ opacity: .55; }}
    .xref-soft code {{ opacity: .85; }}
    .transcript-row {{ display: grid; grid-template-columns: 5rem minmax(0, 1fr); gap: 1rem; align-items: start; margin: 0 0 .5rem; }}
    .transcript-time {{ font-family: var(--font-mono); text-align: right; white-space: nowrap; color: var(--accent); user-select: none; -webkit-user-select: none; }}
    .transcript-text {{ min-width: 0; }}
    .content-image {{ max-width: 100%; height: auto; display: block; }}
    .content-figure {{ margin: 1rem 0; }}
    .content-caption {{ font-family: var(--font-ui); font-size: .82rem; color: var(--muted); margin-top: .3rem; font-style: italic; }}
    .contrib-badge, .readtime-badge {{ font: 600 .72rem var(--font-ui); color: var(--accent); background: var(--accent-soft); border: 1px solid color-mix(in srgb, var(--accent) 25%, var(--border)); border-radius: 999px; padding: .12rem .5rem; margin-left: .3rem; vertical-align: middle; }}
    .readtime-badge {{ color: var(--text); }}
    .btn {{ padding: .4rem .7rem; border: 1px solid var(--border); background: var(--surface); color: var(--text); cursor: pointer; font: inherit; font-size: .85rem; font-family: var(--font-ui); }}
    .btn-primary {{ background: var(--accent); color: white; border-color: var(--accent); }}
    .btn-danger {{ color: var(--danger); border-color: var(--danger); }}
    .btn-active {{ background: var(--accent-soft) !important; color: var(--accent) !important; border-color: var(--accent) !important; }}
    .readaloud-bar {{ position: fixed; bottom: 0; left: 0; right: 0; z-index: 50; display: flex; align-items: center; gap: .75rem; padding: .6rem 2rem; background: var(--surface); border-top: 1px solid var(--border); font-family: var(--font-ui); font-size: .85rem; box-shadow: 0 -4px 20px rgba(0,0,0,.1); }}
    .readaloud-bar[hidden] {{ display: none; }}
    .readaloud-status {{ color: var(--muted); min-width: 5rem; }}
    .readaloud-bar select {{ padding: .2rem .3rem; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-alt); color: var(--text); font: inherit; font-size: .82rem; }}
    .ra-select-tip {{ position: fixed; z-index: 60; transform: translate(-50%, -100%); margin-top: -8px; }}
    .ra-select-tip[hidden] {{ display: none; }}
    .ra-select-tip button {{ background: var(--accent); color: #fff; border: 0; border-radius: 6px; padding: .35rem .65rem; font-family: var(--font-ui); font-size: .8rem; cursor: pointer; box-shadow: 0 2px 10px rgba(0,0,0,.25); white-space: nowrap; }}
    .ra-select-tip button:hover {{ filter: brightness(1.08); }}
    .ra-reading {{ background: color-mix(in srgb, var(--accent) 16%, transparent); border-radius: 4px; box-shadow: -3px 0 0 0 var(--accent); }}
    .sidebar-section + .sidebar-section {{ margin-top: 1rem; }}
    .sidebar-section h2 {{ font-size: .9rem; font-family: var(--font-ui); border-bottom: 1px solid var(--border); padding-bottom: .2rem; margin: 0 0 .4rem; }}
    .sidebar-section ul {{ list-style: none; padding: 0; margin: 0; }}
    .sidebar-section li {{ padding: .25rem 0; line-height: 1.3; }}
    .panel-link {{ color: var(--accent); text-decoration: none; font-family: var(--font-ui); font-size: .85rem; display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .panel-link:hover {{ text-decoration: underline; }}
    .why-label {{ color: var(--muted); font-family: var(--font-ui); font-size: .75rem; margin-top: .1rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    #edit-area {{ display: none; }}
    body.editing #content-area {{ display: none; }}
    body.editing .side-panel {{ display: none; }}
    body.editing #edit-area {{ display: block; }}
    body.editing .layout {{ grid-template-columns: minmax(0,1fr); }}
    #edit-textarea {{ width: 100%; min-height: 60vh; padding: .8rem; border: 1px solid var(--border); background: var(--surface); color: var(--text); font: .88rem var(--font-mono); resize: vertical; }}
    .edit-actions {{ display: flex; gap: .5rem; margin-top: .5rem; }}
    .caption-export {{ position: relative; }}
    .caption-export summary {{ list-style: none; }}
    .caption-export summary::-webkit-details-marker {{ display: none; }}
    .caption-export summary::after {{ content: " +"; color: var(--muted); }}
    .caption-export[open] summary {{ border-color: var(--accent); color: var(--accent); }}
    .caption-export[open] summary::after {{ content: " -"; }}
    .caption-export-menu {{ position: absolute; right: 0; top: calc(100% + .3rem); z-index: 30; min-width: 10rem; padding: .25rem 0; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); box-shadow: var(--card-shadow); }}
    .caption-export-menu a {{ display: block; padding: .42rem .7rem; color: var(--text); text-decoration: none; white-space: nowrap; font-family: var(--font-ui); font-size: .82rem; }}
    .caption-export-menu a:hover, .caption-export-menu a:focus-visible {{ background: var(--accent-soft); color: var(--accent); }}
    @media (max-width: 1100px) {{ .layout {{ grid-template-columns: 1fr; }} .side-panel {{ order: 2; }} }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ scroll-behavior: auto; transition: none !important; animation: none !important; }} }}
    .toast-container {{ position: fixed; bottom: 1.5rem; right: 1.5rem; z-index: 100; display: flex; flex-direction: column; gap: 0.5rem; pointer-events: none; }}
    .toast {{ padding: 0.85rem 1.1rem; border-radius: var(--radius); background: var(--surface); border: 1px solid var(--border); box-shadow: 0 8px 28px rgba(0,0,0,0.15); font-family: var(--font-ui); font-size: .88rem; color: var(--text); animation: toast-in var(--motion-duration) ease forwards; pointer-events: auto; max-width: 26rem; display: flex; align-items: center; gap: .5rem; }}
    .toast-success {{ border-left: 4px solid var(--success); }}
    .toast-error {{ border-left: 4px solid var(--danger); }}
    .toast-info {{ border-left: 4px solid var(--accent); }}
    .toast-icon {{ font-size: 1rem; flex-shrink: 0; }}
    @keyframes toast-in {{ from {{ opacity: 0; transform: translateY(1rem) scale(.96); }} to {{ opacity: 1; transform: translateY(0) scale(1); }} }}
    @keyframes toast-out {{ from {{ opacity: 1; transform: translateY(0) scale(1); }} to {{ opacity: 0; transform: translateY(1rem) scale(.96); }} }}
    body.focus-mode .side-panel {{ display: none !important; }}
    body.focus-mode .layout {{ grid-template-columns: minmax(0, 1fr) !important; }}
    .split-edit {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; align-items: start; }}
    .split-edit textarea {{ min-height: 70vh; }}
    .split-edit .preview-pane {{ border: 1px solid var(--border); border-radius: 12px; padding: 0.75rem 1rem; overflow-y: auto; min-height: 70vh; max-height: 80vh; background: var(--bg); color: var(--text); font-family: var(--font-body); font-size: .92rem; line-height: 1.65; }}
    .split-edit .preview-pane :is(h1,h2,h3) {{ font-family: var(--font-ui); margin: .8rem 0 .35rem; }}
    .split-edit .preview-pane h1 {{ font-size: 1.4rem; }}
    .split-edit .preview-pane h2 {{ font-size: 1.2rem; border-bottom: 1px solid var(--border); padding-bottom: .1rem; }}
    .split-edit .preview-pane h3 {{ font-size: 1.05rem; }}
    .split-edit .preview-pane p {{ margin: .35rem 0; }}
    .split-edit .preview-pane code {{ font-family: var(--font-mono); background: rgba(127,127,127,0.08); padding: .08rem .3rem; font-size: .88em; }}
    .split-edit .preview-pane pre {{ background: #f6f8fa; color: #24292e; padding: .5rem .8rem; border-radius: 8px; overflow-x: auto; font-family: var(--font-mono); font-size: .85rem; }}
    .split-edit .preview-pane ul {{ padding-left: 1.5rem; }}
    .split-edit .preview-pane img {{ max-width: 100%; height: auto; border-radius: 8px; }}
    .focus-btn {{ font-size: .82rem; }}
    .kbd {{ display: inline-flex; align-items: center; justify-content: center; min-width: 1.5rem; height: 1.4rem; padding: 0 .3rem; border-radius: 4px; background: var(--surface-alt); border: 1px solid var(--border); font-family: var(--font-ui); font-size: .72rem; color: var(--muted); }}
    .shortcuts-help {{ position: fixed; bottom: 4rem; right: 1.5rem; z-index: 99; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: 0 8px 32px rgba(0,0,0,0.18); padding: 1rem 1.2rem; min-width: 18rem; font-family: var(--font-ui); }}
    .shortcuts-help h3 {{ margin: 0 0 .6rem; font-size: .9rem; }}
    .shortcuts-help table {{ width: 100%; border-collapse: collapse; font-size: .82rem; }}
    .shortcuts-help td {{ padding: .25rem .35rem; }}
    .shortcuts-help td:last-child {{ color: var(--muted); text-align: right; }}
    .menubar {{ display: flex; align-items: center; background: var(--surface); border-bottom: 1px solid var(--border); font-family: var(--font-ui); font-size: .82rem; user-select: none; min-height: 1.8rem; margin: 0; padding: 0 2rem; position: sticky; top: 0; z-index: 25; }}
    .menubar-left {{ display: flex; align-items: stretch; }}
    .menu-item {{ position: relative; }}
    .menu-trigger {{ background: none; border: none; color: var(--text); font: inherit; padding: .25rem .6rem; cursor: pointer; border-radius: 0; }}
    .menu-trigger:hover, .menu-trigger[aria-expanded="true"] {{ background: var(--accent-soft); }}
    .menu-dropdown {{ display: none; position: absolute; top: 100%; left: 0; min-width: 13rem; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; box-shadow: 0 8px 24px rgba(0,0,0,.12); z-index: 200; padding: .3rem 0; }}
    .menu-dropdown.open {{ display: block; }}
    .menu-dropdown button {{ display: block; width: 100%; text-align: left; background: none; border: none; color: var(--text); font: inherit; font-size: .82rem; padding: .45rem 1rem; cursor: pointer; }}
    .menu-label {{ display: block; }}
    .menu-desc {{ display: block; margin-top: .12rem; font-size: .72rem; color: var(--muted); line-height: 1.3; white-space: normal; }}
    .menu-dropdown button:hover {{ background: var(--accent-soft); }}
    .menu-dropdown hr {{ border: none; border-top: 1px solid var(--border); margin: .3rem .5rem; }}
    .menu-dropdown .label {{ display: block; padding: .2rem 1rem .1rem; font-size: .72rem; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }}
    .menubar-right {{ display: flex; align-items: center; padding: 0 .5rem; gap: .4rem; margin-left: auto; }}
    .version-badge {{ font: 500 .7rem var(--font-ui); color: var(--muted); background: var(--surface-alt); border: 1px solid var(--border); border-radius: 999px; padding: .1rem .5rem; margin-left: .3rem; letter-spacing: .02em; }}
    .about-dialog {{ max-width: 36rem; }}
    .about-dialog table {{ width: 100%; border-collapse: collapse; font-size: .85rem; }}
    .about-dialog td {{ padding: .35rem .5rem; border-bottom: 1px solid var(--border); }}
    .about-dialog td:first-child {{ color: var(--muted); white-space: nowrap; width: 30%; }}
    .about-dialog pre {{ background: var(--surface-alt); padding: .6rem .8rem; border-radius: 6px; font-family: var(--font-mono); font-size: .8rem; overflow-x: auto; white-space: pre-wrap; }}
    .about-dialog .install-active {{ background: var(--accent-soft); border: 1px solid var(--accent); border-radius: 6px; padding: .6rem .8rem; margin: .5rem 0; }}
    .about-dialog .install-other {{ opacity: .65; padding: .4rem .8rem; margin: .3rem 0; border-left: 2px solid var(--border); }}
  </style>
</head>
<body>
{menubar_html(version=version)}
  {about_dialog_html()}
  <a class="skip-link" href="#content">Skip to content</a>
  <div class="page">
    <header class="site-header">
      <a class="back-link" href="/">← All notes</a>
      <div class="site-nav">
        <button id="focus-btn" class="btn focus-btn" type="button" title="Toggle focus mode">Focus</button>
        <button id="edit-btn" class="btn" type="button">Edit</button>
        <button id="rename-btn" class="btn" type="button" title="Rename this note and update wiki links">Rename</button>
        <button id="readaloud-btn" class="btn" type="button" title="Read this page aloud (browser TTS)">Read Aloud</button>
        {caption_export_html}
        <button type="button" class="btn btn-danger" data-trash-item="{escape(item.id, quote=True)}">Delete</button>
      </div>
    </header>
    <h1 class="title">{escape(ctx.title)}</h1>
    <p class="byline">{escape(item.type.value.replace("_", " "))} · <code>{escape(item.id)}</code> · {escape(item.provenance.authorship or "unknown author")}{score_badge}{read_time_badge} {contributor_badge}</p>
    <div class="layout">
      <aside class="side-panel">
        <form class="sidebar-search" action="/search" method="get" role="search"><label for="sidebar-query" class="meta">Search</label><input id="sidebar-query" name="q" type="search" placeholder="Search notes"></form>
        {toc_html or ""}
        <div class="sidebar-section" style="margin-top:{".8rem" if has_toc else "0"}">
          <h2>See also</h2>
          <h3 style="font-size:.85rem;margin:.3rem 0 .15rem;color:var(--muted)">Related</h3>
          <ul>{related_list}</ul>
          <h3 style="font-size:.85rem;margin:.5rem 0 .15rem;color:var(--muted)">Depends on</h3>
          <ul>{deps_list}</ul>
        </div>
      </aside>
      <main id="content">
        <article class="article">
          <section id="content-area">
            <div class="body">{html_body}</div>
          </section>
          <section id="edit-area">
            <div class="split-edit">
              <div class="editor-textarea-wrap">
                <textarea id="edit-textarea" aria-label="Edit markdown">{escaped_raw}</textarea>
                <div id="wiki-suggestions" class="wiki-suggestions" role="listbox" aria-label="Wiki link suggestions"></div>
              </div>
              <div id="live-preview" class="preview-pane" aria-label="Live preview"><p class="meta" style="text-align:center;padding-top:4rem">Preview appears here as you type.</p></div>
            </div>
            <div class="alias-editor" id="alias-editor" data-aliases="{aliases_json}">
              <strong>Aliases</strong><span class="meta"> Names that can resolve to this note in <code>[[wiki links]]</code>.</span>
              <div id="alias-list" aria-live="polite"></div>
              <form id="alias-form"><label class="sr-only" for="alias-input">Add alias</label><input id="alias-input" type="text" placeholder="Add an alias"><button class="btn" type="submit">Add alias</button></form>
            </div>
            <div class="edit-actions"><button id="save-btn" class="btn btn-primary">Save</button><button id="cancel-btn" class="btn">Cancel</button><button id="copy-md-btn" class="btn" type="button" title="Copy raw markdown to clipboard">Copy markdown</button><label style="font-size:.82rem;font-family:var(--font-ui);display:flex;align-items:center;gap:.35rem;margin-left:.5rem"><input id="human-edit-cbox" type="checkbox" checked> Human edited</label><span id="edit-status" class="meta" role="status"></span></div>
            <p class="meta">Paste or drop images to embed them. Edit the text inside <code>![your caption here](...)</code> to set a caption.</p>
          </section>
        </article>
      </main>
      <aside class="side-panel">
        <div class="infobox">
          <div class="infobox-title">{escape(ctx.title)}</div>
          <table>
            {infobox_rows}
            <tr class="tag-row"><th>Tags</th><td><div class="chips">{tag_list}</div></td></tr>
          </table>
        </div>
        <div class="sidebar-section">
          <h2>Similar</h2>
          <ul>{similar_list}</ul>
        </div>
        <div class="sidebar-section">
          <h2>Referenced by</h2>
          <ul>{backlinks_list}</ul>
        </div>
      </aside>
    </div>
  </div>
  {toast_container_html()}
  <div id="ra-select-tip" class="ra-select-tip" hidden><button type="button">&#9654; Read selection</button></div>
  <div id="readaloud-bar" class="readaloud-bar" hidden aria-live="polite">
    <span id="readaloud-status" class="readaloud-status">Reading...</span>
    <button id="readaloud-playpause" class="btn" type="button">Pause</button>
    <button id="readaloud-stop" class="btn" type="button">Stop</button>
    <label style="display:flex;align-items:center;gap:.3rem;margin-left:auto">
      Speed:
      <select id="readaloud-rate">
        <option value="0.5">0.5x</option>
        <option value="0.8">0.8x</option>
        <option value="1" selected>1x</option>
        <option value="1.5">1.5x</option>
        <option value="2">2x</option>
      </select>
    </label>
  </div>
  <div id="shortcuts-help" class="shortcuts-help" hidden>
    <h3>Keyboard shortcuts</h3>
    <table>
      <tr><td><span class="kbd">E</span></td><td>Toggle edit mode</td></tr>
      <tr><td><span class="kbd">Ctrl+S</span></td><td>Save changes</td></tr>
      <tr><td><span class="kbd">Esc</span></td><td>Close panels / cancel edit</td></tr>
      <tr><td><span class="kbd">/</span></td><td>Focus search</td></tr>
            <tr><td><span class="kbd">?</span></td><td>Show this help</td></tr>
    </table>
  </div>
{item_page_script(item.id)}
</body>
</html>
"""
