"""Media library page for durable, content-addressed uploads."""

from __future__ import annotations

from html import escape
import json

from ..components import about_dialog_html, menubar_html, toast_container_html
from ..skin import PAPER_SKIN, Skin, skin_vars


def _size(value: object) -> str:
    try:
        amount = int(value or 0)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if amount < 1024 or unit == "GB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount / (1024 ** (('KB', 'MB', 'GB').index(unit) + 1)):.1f} {unit}"
    return ""


def render_media_html(assets: list[dict], skin: Skin = PAPER_SKIN, *, version: str = "") -> str:
    cards: list[str] = []
    image_count = 0
    total_bytes = 0
    for asset in assets:
        asset_id = escape(str(asset.get("id", "")), quote=True)
        name = escape(str(asset.get("original_name") or asset_id))
        media_type = escape(str(asset.get("media_type") or "asset"))
        preview = escape(str(asset.get("preview_url") or asset.get("url") or ""), quote=True)
        url = escape(str(asset.get("url") or ""), quote=True)
        total_bytes += int(asset.get("size") or 0) if str(asset.get("size") or "0").isdigit() else 0
        dimensions = ""
        if asset.get("width") and asset.get("height"):
            dimensions = f"{escape(str(asset['width']))} × {escape(str(asset['height']))}"
        details = " · ".join(part for part in (dimensions, _size(asset.get("size")), media_type) if part)
        if asset.get("is_image"):
            image_count += 1
            visual = f'<img src="{preview}" alt="{name}" loading="lazy" decoding="async"><span class="media-kind">IMAGE</span>'
        else:
            visual = f'<div class="media-file" aria-label="{media_type}"><span class="file-glyph">↗</span><span>FILE</span></div>'
        markdown = f"![{str(asset.get('original_name') or 'image')}](/asset/{asset_id})"
        cards.append(
            f'<article class="media-card" data-media-card data-media-name="{name.lower()}"><div class="media-preview">{visual}'
            f'<a class="preview-link" href="{preview}" target="_blank" rel="noopener" aria-label="Open preview of {name}">View</a></div>'
            f'<div class="media-card-body"><div class="media-card-title"><strong title="{name}">{name}</strong><span class="media-index">{len(cards):02d}</span></div>'
            f'<span class="media-meta">{details}</span><div class="media-actions"><button type="button" class="action-primary" data-copy-embed="{escape(markdown, quote=True)}"><span>＋</span> Copy embed</button>'
            f'<a href="{url}" target="_blank" rel="noopener">Open original <span>↗</span></a></div></div></article>'
        )
    gallery = "".join(cards) or '<div class="empty-state"><div class="empty-orbit">✦</div><h2>Your media shelf is empty</h2><p>Drop an image here or use the uploader above. Uploaded files stay local and can be reused in any note.</p></div>'
    payload = json.dumps({"assets": len(assets), "images": image_count, "bytes": total_bytes})
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Media library · TextStrata</title><style>
:root {{{skin_vars(skin)}}}
* {{ box-sizing:border-box }} body {{ margin:0; min-height:100vh; background:radial-gradient(circle at 84% 0%, color-mix(in srgb, var(--accent-soft) 62%, transparent), transparent 32rem),var(--bg); color:var(--text); font-family:var(--font-body); font-size:var(--font-scale); }}
.menubar {{ display:flex; align-items:center; background:var(--surface); border-bottom:1px solid var(--border); font-family:var(--font-ui); font-size:.82rem; user-select:none; min-height:1.8rem; margin:0; padding:0 2rem; position:sticky; top:0; z-index:25; }}
.menubar-left {{ display:flex; align-items:stretch; }} .menubar-right {{ display:flex; align-items:center; padding:0 .5rem; gap:.4rem; margin-left:auto; }}
.menu-item {{ position:relative; }} .menu-trigger {{ background:none; border:none; color:var(--text); font:inherit; padding:.25rem .6rem; cursor:pointer; border-radius:0; }} .menu-trigger:hover,.menu-trigger[aria-expanded="true"] {{ background:var(--accent-soft); }}
.menu-dropdown {{ display:none; position:absolute; top:100%; left:0; min-width:13rem; background:var(--surface); border:1px solid var(--border); border-radius:6px; box-shadow:0 8px 24px rgba(0,0,0,.12); z-index:200; padding:.3rem 0; }} .menu-dropdown.open {{ display:block; }} .menu-dropdown button {{ display:block; width:100%; text-align:left; background:none; border:none; color:var(--text); font:inherit; font-size:.82rem; padding:.45rem 1rem; cursor:pointer; }} .menu-dropdown button:hover {{ background:var(--accent-soft); }} .menu-dropdown hr {{ border:none; border-top:1px solid var(--border); margin:.3rem .5rem; }} .menu-label {{ display:block; }} .menu-desc {{ display:block; margin-top:.12rem; font-size:.72rem; color:var(--muted); line-height:1.3; white-space:normal; }} .version-badge {{ font:500 .7rem var(--font-ui); color:var(--muted); background:var(--surface-alt); border:1px solid var(--border); border-radius:999px; padding:.1rem .5rem; }}
.page {{ width:min(var(--max-width),100%); margin:0 auto; padding:clamp(2.3rem,5vw,5rem) clamp(1rem,3vw,2.4rem) 5rem; }}
.media-head {{ display:flex; justify-content:space-between; gap:2rem; align-items:end; margin:0 0 2rem; }} .media-kicker {{ display:flex; align-items:center; gap:.55rem; color:var(--accent); font:700 .68rem var(--font-ui); letter-spacing:.14em; text-transform:uppercase; }} .media-kicker::before {{ content:''; width:1.7rem; height:2px; background:var(--accent); }}
.media-head h1 {{ max-width:18ch; margin:.5rem 0 .6rem; font-size:clamp(2.35rem,6vw,4.7rem); line-height:.95; letter-spacing:-.06em; font-weight:800; }} .media-intro {{ max-width:48ch; margin:0; color:var(--muted); font:1rem/1.55 var(--font-ui); }} .media-head-links {{ display:flex; gap:.5rem; align-items:center; flex-shrink:0; }} .media-head-links a {{ color:var(--text); text-decoration:none; font:600 .78rem var(--font-ui); }} .media-head-links a:hover {{ color:var(--accent); }} .media-head-links a:first-child {{ padding:.65rem .85rem; background:var(--surface); border:1px solid var(--border); border-radius:999px; }}
.media-stats {{ display:flex; flex-wrap:wrap; gap:.65rem; margin:0 0 1.7rem; }} .stat {{ display:flex; align-items:baseline; gap:.42rem; padding:.6rem .8rem; background:color-mix(in srgb,var(--surface) 82%,transparent); border:1px solid var(--border); border-radius:999px; font-family:var(--font-ui); }} .stat strong {{ color:var(--text); font-size:.9rem; }} .stat span {{ color:var(--muted); font-size:.72rem; }}
.upload-bar {{ display:grid; grid-template-columns:1fr auto; gap:1rem; align-items:center; margin:0 0 2.4rem; padding:1.15rem 1.25rem; background:linear-gradient(120deg,var(--surface),color-mix(in srgb,var(--accent-soft) 38%,var(--surface))); border:1px solid color-mix(in srgb,var(--accent) 32%,var(--border)); border-radius:18px; box-shadow:0 16px 35px rgba(15,23,42,.06); }} .upload-copy {{ display:grid; gap:.25rem; }} .upload-copy strong {{ font:700 .9rem var(--font-ui); }} .upload-copy span {{ color:var(--muted); font: .78rem var(--font-ui); }} .upload-controls {{ display:flex; align-items:center; gap:.6rem; }} .upload-controls input {{ max-width:15rem; font: .78rem var(--font-ui); }} .upload-controls button {{ border:0; border-radius:999px; padding:.7rem 1rem; background:var(--accent); color:white; font:700 .78rem var(--font-ui); cursor:pointer; box-shadow:0 6px 14px color-mix(in srgb,var(--accent) 28%,transparent); }} .upload-controls button:hover {{ filter:brightness(1.08); }} .upload-status {{ grid-column:1/-1; color:var(--muted); font:.75rem var(--font-ui); min-height:1em; }}
.media-toolbar {{ display:flex; align-items:center; justify-content:space-between; gap:1rem; margin:0 0 .8rem; font-family:var(--font-ui); }} .media-toolbar h2 {{ margin:0; font-size:.88rem; letter-spacing:.02em; }} .media-toolbar label {{ display:flex; align-items:center; gap:.45rem; color:var(--muted); font-size:.75rem; }} .media-toolbar input {{ width:min(16rem,45vw); padding:.52rem .7rem; border:1px solid var(--border); border-radius:999px; background:var(--surface); color:var(--text); font:inherit; }}
.media-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(235px,1fr)); gap:1.15rem; }} .media-card {{ overflow:hidden; background:var(--surface); border:1px solid color-mix(in srgb,var(--border) 78%,transparent); border-radius:18px; box-shadow:var(--card-shadow); transition:transform var(--motion-duration),box-shadow var(--motion-duration),border-color var(--motion-duration); }} .media-card:hover {{ transform:translateY(-4px); border-color:color-mix(in srgb,var(--accent) 45%,var(--border)); box-shadow:0 20px 42px rgba(15,23,42,.13); }}
.media-preview {{ position:relative; aspect-ratio:1.15; display:grid; place-items:center; background:linear-gradient(135deg,var(--surface-alt),color-mix(in srgb,var(--accent-soft) 40%,var(--surface-alt))); overflow:hidden; }} .media-preview::after {{ content:''; position:absolute; inset:0; background:linear-gradient(180deg,transparent 58%,rgba(0,0,0,.28)); opacity:.75; pointer-events:none; }} .media-preview img {{ width:100%; height:100%; object-fit:cover; transition:transform 500ms ease; }} .media-card:hover .media-preview img {{ transform:scale(1.045); }} .media-kind {{ position:absolute; z-index:1; top:.7rem; left:.7rem; padding:.28rem .42rem; border:1px solid rgba(255,255,255,.45); border-radius:999px; color:#fff; background:rgba(15,23,42,.48); font:700 .58rem var(--font-mono); letter-spacing:.08em; }} .preview-link {{ position:absolute; z-index:2; right:.7rem; bottom:.65rem; padding:.38rem .58rem; border:1px solid rgba(255,255,255,.55); border-radius:999px; color:#fff; background:rgba(15,23,42,.48); text-decoration:none; font:700 .68rem var(--font-ui); }} .preview-link:hover {{ background:var(--accent); }} .media-file {{ display:grid; place-items:center; gap:.45rem; color:var(--muted); font:700 .72rem var(--font-mono); letter-spacing:.12em; }} .file-glyph {{ display:grid; place-items:center; width:3.5rem; height:3.5rem; border:1px solid var(--border); border-radius:18px; color:var(--accent); background:var(--surface); font-size:1.6rem; }}
.media-card-body {{ display:grid; gap:.48rem; padding:.95rem 1rem 1rem; font-family:var(--font-ui); }} .media-card-title {{ display:flex; align-items:center; gap:.55rem; }} .media-card-body strong {{ min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:.86rem; }} .media-index {{ margin-left:auto; color:var(--muted); font:600 .64rem var(--font-mono); }} .media-meta {{ color:var(--muted); font-size:.7rem; }} .media-actions {{ display:flex; flex-wrap:wrap; gap:.45rem; margin-top:.35rem; }} .media-actions a,.media-actions button {{ border:1px solid var(--border); background:transparent; color:var(--text); border-radius:999px; padding:.45rem .62rem; text-decoration:none; cursor:pointer; font:600 .7rem var(--font-ui); }} .media-actions a:hover,.media-actions button:hover {{ border-color:var(--accent); color:var(--accent); }} .media-actions .action-primary {{ border-color:color-mix(in srgb,var(--accent) 45%,var(--border)); background:var(--accent-soft); color:var(--accent); }} .media-actions span {{ font-size:.9rem; }} .empty-state {{ grid-column:1/-1; padding:5rem 1.5rem; text-align:center; background:var(--surface); border:1px dashed color-mix(in srgb,var(--accent) 35%,var(--border)); border-radius:22px; }} .empty-state h2 {{ margin:.8rem 0 .35rem; font-size:1.3rem; }} .empty-state p {{ max-width:38ch; margin:0 auto; color:var(--muted); font: .86rem/1.5 var(--font-ui); }} .empty-orbit {{ display:grid; place-items:center; width:4rem; height:4rem; margin:auto; border:1px solid var(--accent); border-radius:50%; color:var(--accent); font-size:1.5rem; box-shadow:0 0 0 .5rem var(--accent-soft); }}
@media (max-width:700px) {{ .menubar {{ padding:0 .65rem; }} .menu-trigger {{ padding:0 .55rem; font-size:.7rem; }} .menu-trigger::after {{ display:none; }} .media-head {{ display:block; }} .media-head h1 {{ font-size:3.1rem; }} .media-head-links {{ margin-top:1.2rem; }} .upload-bar {{ grid-template-columns:1fr; }} .upload-controls {{ display:grid; grid-template-columns:1fr auto; }} .upload-controls input {{ max-width:none; min-width:0; }} .media-toolbar {{ align-items:start; flex-direction:column; }} .media-toolbar input {{ width:100%; }} .media-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); gap:.7rem; }} .media-card-body {{ padding:.75rem; }} .media-actions {{ display:grid; }} .media-actions a,.media-actions button {{ text-align:center; }} }}
</style></head><body>{menubar_html(version=version)}<main class="page"><header class="media-head"><div><div class="media-kicker">Media</div><h1>Media library</h1><p class="media-intro">Images and uploaded files from your workspace, ready to reuse in notes.</p></div><nav class="media-head-links" aria-label="Media actions"><a href="/">← Library</a><a href="/new">New note <span>↗</span></a></nav></header><div class="media-stats"><div class="stat"><strong>{len(assets)}</strong><span>stored assets</span></div><div class="stat"><strong>{image_count}</strong><span>images</span></div><div class="stat"><strong>{_size(total_bytes) or '0 B'}</strong><span>on disk</span></div></div><form id="media-upload-form" class="upload-bar"><div class="upload-copy"><strong>Add media</strong><span>Upload images from this workspace.</span></div><div class="upload-controls"><input id="media-upload" type="file" accept="image/*" multiple aria-label="Choose images"><button type="submit">Upload images</button></div><span id="media-upload-status" class="upload-status" role="status" aria-live="polite"></span></form><div class="media-toolbar"><h2>All media <span class="meta" id="media-count">{len(assets)}</span></h2><label for="media-filter">Find an asset <input id="media-filter" type="search" placeholder="Search filenames" autocomplete="off"></label></div><section class="media-grid" aria-label="Uploaded media">{gallery}</section></main>{about_dialog_html()}{toast_container_html()}<script>
const mediaSummary={payload};
document.getElementById('media-upload-form').addEventListener('submit',async(event)=>{{
  event.preventDefault(); const input=document.getElementById('media-upload'); const status=document.getElementById('media-upload-status');
  if(!input.files.length){{status.textContent='Choose one or more images first.';return;}}
  status.textContent='Uploading…';
  try {{ for(const file of input.files){{const form=new FormData();form.append('asset',file,file.name);const response=await fetch('/api/asset/upload',{{method:'POST',body:form}});if(!response.ok)throw new Error('Upload failed for '+file.name);}} window.location.reload(); }}
  catch(error){{status.textContent=error.message;}}
}});
document.getElementById('media-filter').addEventListener('input',(event)=>{{const query=event.target.value.toLowerCase().trim();let visible=0;document.querySelectorAll('[data-media-card]').forEach((card)=>{{const match=!query||card.dataset.mediaName.includes(query);card.hidden=!match;if(match)visible++;}});document.getElementById('media-count').textContent=visible;}});
document.querySelectorAll('[data-copy-embed]').forEach((button)=>button.addEventListener('click',async()=>{{
  try {{ await navigator.clipboard.writeText(button.dataset.copyEmbed); button.textContent='Copied'; setTimeout(()=>button.textContent='Copy embed',1200); }}
  catch {{ window.prompt('Copy this Markdown embed',button.dataset.copyEmbed); }}
}}));
document.querySelectorAll('[data-menu]').forEach((trigger)=>trigger.addEventListener('click',()=>{{const menu=document.getElementById(trigger.dataset.menu);const open=!menu.classList.contains('open');document.querySelectorAll('.menu-dropdown').forEach((node)=>node.classList.remove('open'));menu.classList.toggle('open',open);trigger.setAttribute('aria-expanded',String(open));}}));
document.addEventListener('keydown',(event)=>{{if(event.key==='Escape')document.querySelectorAll('.menu-dropdown').forEach((node)=>node.classList.remove('open'));}}); document.addEventListener('click',(event)=>{{if(!event.target.closest('.menu-item'))document.querySelectorAll('.menu-dropdown').forEach((node)=>node.classList.remove('open'));}});
document.querySelectorAll('[data-action]').forEach((button)=>button.addEventListener('click',()=>{{const action=button.dataset.action;if(action==='new-note')location.href='/new';else if(action==='setup')location.href='/setup';else if(action==='media')location.href='/media';else if(action==='graph')location.href='/graph';else if(action==='focus-mode')document.body.classList.toggle('focus-mode');else if(action==='docs')location.href='/';else if(action==='about'||action==='system-info'){{const dialog=document.getElementById('about-dialog');if(dialog)dialog.showModal();}}}}));
document.querySelectorAll('[data-close]').forEach((button)=>button.addEventListener('click',()=>document.getElementById(button.dataset.close)?.close()));
</script></body></html>"""
