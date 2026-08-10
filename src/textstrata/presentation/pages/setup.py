"""Setup and capability center page."""

from __future__ import annotations

import json
from html import escape
from typing import cast

from ..skin import Skin, skin_vars


def render_setup_html(status: dict[str, object], skin: Skin, *, version: str = "") -> str:
    checks = cast(list[dict[str, object]], status.get("required_core_checks", []))
    capabilities = cast(list[dict[str, object]], status.get("optional_capabilities", []))
    check_rows = "".join(
        f'<li class="check-row {"ready" if check.get("available") else "missing"}"><strong>{escape(str(check.get("label", "")))}</strong><span>{escape(str(check.get("detail", "")))}</span></li>'
        for check in checks if isinstance(check, dict)
    )
    cards = "".join(
        f'<article class="capability-card {"ready" if card.get("available") else "optional-missing"}">'
        f'<div class="capability-head"><h3>{escape(str(card.get("label", "")))}</h3><span>{"Ready" if card.get("available") else "Optional"}</span></div>'
        f'<p>{escape(str(card.get("missing_dependency") or "Available on this host."))}</p>'
        f'<code>{escape(str(card.get("install_hint", "")))}</code>'
        f'{"<small>This capability may download a model when first used.</small>" if card.get("model_download_required") else ""}</article>'
        for card in capabilities if isinstance(card, dict)
    )
    payload = json.dumps(status, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Setup · TextStrata</title><style>
:root {{{skin_vars(skin)}}} * {{ box-sizing:border-box }} body {{ margin:0; background:var(--bg); color:var(--text); font-family:var(--font-ui); }}
a {{ color:var(--accent); }} .shell {{ width:min(100% - 2rem, 64rem); margin:0 auto; padding:2rem 0 4rem; }}
.top {{ display:flex; justify-content:space-between; gap:1rem; align-items:center; border-bottom:1px solid var(--border); padding-bottom:1rem; }}
.top a {{ text-decoration:none; }} .hero {{ padding:2rem 0 1.2rem; }} h1 {{ margin:0; font:400 clamp(1.8rem,4vw,2.7rem)/1.1 Georgia,serif; }} h2 {{ margin:0 0 .8rem; font-size:1.05rem; }} h3 {{ margin:0; font-size:.95rem; }} p {{ line-height:1.6; }} .meta {{ color:var(--muted); }}
.panel {{ background:var(--surface); border:1px solid var(--border); padding:1rem 1.15rem; margin:1rem 0; box-shadow:var(--card-shadow); }}
.status-line {{ display:flex; flex-wrap:wrap; gap:.5rem 1rem; align-items:center; }} .pill {{ padding:.25rem .55rem; border:1px solid var(--border); border-radius:999px; font-size:.78rem; }} .pill.ready {{ color:var(--success); border-color:var(--success); }}
.check-list {{ list-style:none; padding:0; margin:0; display:grid; gap:.45rem; }} .check-row {{ display:grid; grid-template-columns:minmax(11rem, .4fr) 1fr; gap:.75rem; padding:.5rem 0; border-bottom:1px solid var(--border); font-size:.83rem; }} .check-row span {{ color:var(--muted); }} .check-row.missing strong {{ color:var(--danger); }}
.capability-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(15rem,1fr)); gap:.75rem; }} .capability-card {{ border:1px solid var(--border); padding:.8rem; background:var(--surface-alt); }} .capability-head {{ display:flex; justify-content:space-between; gap:.5rem; }} .capability-head span {{ color:var(--success); font-size:.75rem; }} .optional-missing .capability-head span {{ color:var(--warning); }} .capability-card p,.capability-card small {{ display:block; color:var(--muted); font-size:.78rem; }} code {{ display:block; overflow:auto; padding:.45rem; background:var(--bg); font: .75rem var(--font-mono); white-space:pre-wrap; }} .actions {{ display:flex; flex-wrap:wrap; gap:.6rem; }} .btn {{ padding:.55rem .8rem; border:1px solid var(--border); background:var(--surface-alt); color:var(--text); text-decoration:none; cursor:pointer; }} .btn-primary {{ background:var(--accent); border-color:var(--accent); color:white; }}
@media (max-width:600px) {{ .shell {{ width:min(100% - 1.25rem,64rem); }} .check-row {{ grid-template-columns:1fr; gap:.15rem; }} }}
</style></head><body><main class="shell"><header class="top"><a href="/"><strong>TextStrata</strong></a><span class="meta">Setup &amp; capabilities · {escape(version)}</span></header>
<section class="hero"><h1>Make this workspace yours</h1><p class="meta">The core stays local and deterministic. Add optional capability packs only when you need them.</p><div class="status-line"><span class="pill {"ready" if status.get("core_ready") else ""}">{"Core ready" if status.get("core_ready") else "Core needs attention"}</span><span class="meta">{escape(str(status.get("workspace", "")))}</span><span class="meta">{status.get("item_count", 0)} notes</span></div></section>
<section class="panel"><h2>Core readiness</h2><ul class="check-list">{check_rows}</ul><div class="actions" style="margin-top:1rem"><button id="initialize-workspace" class="btn btn-primary" type="button">Initialize workspace</button><a class="btn" href="/">Return to library</a></div><p id="setup-status" class="meta" role="status" aria-live="polite"></p></section>
<section class="panel"><h2>Optional capabilities</h2><p class="meta">Unavailable optional tools do not make the core app unhealthy. Installation is always explicit.</p><div class="capability-grid">{cards}</div></section>
<section class="panel"><h2>Safe next steps</h2><p><a href="/docs/installation">Installation and portability</a> · <a href="/docs/backup-restore">Backup and restore</a></p><p class="meta">Restore and maintenance operations remain under Advanced operations and are not started from setup.</p></section>
</main><script>const setupStatus={payload};document.getElementById('initialize-workspace').addEventListener('click',async()=>{{const status=document.getElementById('setup-status');status.textContent='Initializing workspace…';try{{const response=await fetch('/api/textstrata/setup/initialize',{{method:'POST',headers:{{'Content-Type':'application/json'}}}});const data=await response.json();if(!response.ok)throw new Error(data.message||data.error||'Initialization failed');status.textContent=data.created&&data.created.length?'Workspace initialized.':'Workspace was already initialized.';}}catch(error){{status.textContent=error.message;}}}});</script></body></html>"""
