"""Focused New Note page composition."""

from __future__ import annotations

from ..browser_assets import client_asset_tag
from ..skin import Skin, skin_vars


def render_new_note_html(skin: Skin, *, version: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>New Note - TextStrata</title>
  <style>
    :root {{ {skin_vars(skin)} }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; min-height: 100%; background: var(--bg); color: var(--text); font: var(--font-scale)/1.55 var(--font-ui); }}
    body {{ min-height: 100vh; }}
    button, input, textarea, select {{ font: inherit; }}
    button, a {{ -webkit-tap-highlight-color: transparent; }}
    a {{ color: var(--accent); }}
    :focus-visible {{ outline: 3px solid color-mix(in srgb, var(--accent) 45%, transparent); outline-offset: 2px; }}
    [hidden] {{ display: none !important; }}
    .skip-link {{ position: absolute; left: -9999px; top: 0; }}
    .skip-link:focus {{ left: 1rem; top: 1rem; z-index: 30; padding: .45rem .7rem; background: var(--surface); border: 1px solid var(--border); }}
    .task-bar {{ min-height: 3.25rem; background: var(--surface); border-bottom: 1px solid var(--border); }}
    .task-bar-inner {{ width: min(100% - 2rem, 66rem); min-height: 3.25rem; margin: 0 auto; display: flex; align-items: center; justify-content: space-between; gap: 1rem; }}
    .brand {{ display: flex; align-items: baseline; gap: .55rem; min-width: 0; color: var(--text); text-decoration: none; }}
    .brand strong {{ font-size: .94rem; }}
    .brand span {{ color: var(--muted); font-size: .76rem; white-space: nowrap; }}
    .bar-actions {{ display: flex; align-items: center; gap: .45rem; }}
    .version {{ color: var(--muted); font-size: .7rem; }}
    .workspace {{ width: min(100% - 2rem, 52rem); margin: 2rem auto 4rem; background: var(--surface); border: 1px solid var(--border); box-shadow: var(--card-shadow); }}
    .page-head {{ padding: 1.65rem 2rem 1rem; border-bottom: 1px solid var(--border); }}
    .page-head h1 {{ margin: 0; font: 400 1.8rem/1.2 Georgia, "Times New Roman", serif; letter-spacing: 0; }}
    .page-head p {{ margin: .3rem 0 0; color: var(--muted); font-size: .88rem; }}
    .form-body {{ padding: 1.5rem 2rem 2rem; }}
    .source-tabs {{ display: flex; border-bottom: 1px solid var(--border); margin-bottom: 1.45rem; }}
    .source-tab {{ min-height: 2.65rem; padding: .55rem 1.1rem; margin: 0 0 -1px; border: 1px solid transparent; border-bottom: 0; background: var(--surface-alt); color: var(--text); cursor: pointer; font-weight: 650; font-size: .9rem; }}
    .source-tab:hover {{ background: var(--surface); }}
    .source-tab[aria-selected="true"] {{ background: var(--surface); border-color: var(--border); }}
    .source-panel {{ min-height: 0; }}
    .field {{ margin-bottom: 1.15rem; }}
    .field label {{ display: block; margin-bottom: .35rem; font-weight: 650; font-size: .9rem; }}
    .optional {{ color: var(--muted); font-weight: 400; font-size: .8rem; }}
    .hint {{ display: block; min-height: 1.25rem; margin-top: .35rem; color: var(--muted); font-size: .78rem; }}
    input[type="text"], textarea, select {{ width: 100%; padding: .58rem .7rem; border: 1px solid var(--border); border-radius: 2px; background: var(--surface); color: var(--text); }}
    input:focus, textarea:focus, select:focus {{ outline: none; border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }}
    input:disabled, textarea:disabled {{ background: var(--surface-alt); color: var(--muted); cursor: not-allowed; }}
    textarea {{ min-height: 5rem; resize: vertical; }}
    #ingest-content {{ min-height: 13rem; font-family: var(--font-mono); font-size: .86rem; }}
    .drop-shell {{ padding: 1rem; border: 1px solid var(--border); background: var(--surface-alt); }}
    .drop-zone {{ min-height: 9.5rem; padding: 1.6rem 1rem; display: grid; place-items: center; text-align: center; border: 2px dashed var(--border); background: var(--surface); transition: border-color var(--motion-duration) ease, background var(--motion-duration) ease; }}
    .drop-zone.is-dragging {{ border-color: var(--accent); background: var(--accent-soft); }}
    .drop-zone strong {{ display: block; margin-bottom: .2rem; font-size: .98rem; }}
    .drop-zone p {{ max-width: 34rem; margin: 0 0 .8rem; color: var(--muted); font-size: .76rem; }}
    #ingest-file {{ position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); clip-path: inset(50%); }}
    .file-options {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 1.25rem; align-items: end; margin-top: 1rem; padding-top: 1rem; border-top: 1px solid var(--border); }}
    .file-options .field {{ margin: 0; }}
    .check-field {{ display: flex; align-items: center; gap: .5rem; min-height: 2.5rem; font-size: .86rem; }}
    .check-field label {{ margin: 0; }}
    .selected-files {{ margin-top: .8rem; color: var(--muted); font-size: .78rem; }}
    .selected-files ul {{ margin: .25rem 0 0; padding-left: 1.15rem; }}
    .selected-files li {{ padding: .08rem 0; }}
    .selected-files li span {{ color: var(--muted); }}
    .metadata {{ margin-top: 1.65rem; padding-top: 1.35rem; border-top: 1px solid var(--border); }}
    .metadata h2 {{ margin: 0 0 1rem; font-size: .72rem; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }}
    .form-actions {{ display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-top: 1.75rem; padding-top: 1.25rem; border-top: 1px solid var(--border); }}
    .primary-actions {{ display: flex; align-items: center; gap: .8rem; margin-left: auto; }}
    .btn {{ min-height: 2.35rem; padding: .45rem .85rem; display: inline-flex; align-items: center; justify-content: center; border: 1px solid var(--border); border-radius: 2px; background: var(--surface-alt); color: var(--text); text-decoration: none; cursor: pointer; font-size: .86rem; font-weight: 650; }}
    .btn:hover {{ background: color-mix(in srgb, var(--surface-alt) 70%, var(--border)); }}
    .btn-primary {{ min-width: 8.5rem; background: var(--accent); border-color: var(--accent); color: white; }}
    .btn-primary:hover {{ background: color-mix(in srgb, var(--accent) 85%, black); }}
    .btn:disabled {{ opacity: .58; cursor: wait; }}
    .status {{ min-height: 1.4rem; color: var(--muted); font-size: .82rem; }}
    .status[data-type="success"] {{ color: var(--success); }}
    .status[data-type="error"] {{ color: var(--danger); }}
    .activity {{ margin: 0 2rem 2rem; border-top: 1px solid var(--border); }}
    .activity summary {{ padding: .85rem 0; cursor: pointer; font-weight: 650; font-size: .86rem; }}
    .activity-head {{ display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding-bottom: .65rem; }}
    .activity-head .hint {{ margin: 0; }}
    .queue {{ border-top: 1px solid var(--border); }}
    .job-line {{ padding: .65rem 0; border-bottom: 1px solid var(--border); font-size: .8rem; }}
    .job-line > div {{ display: flex; align-items: center; justify-content: space-between; gap: .6rem; }}
    .job-line p {{ margin: .15rem 0; color: var(--muted); overflow-wrap: anywhere; }}
    .job-status {{ padding: .08rem .35rem; border: 1px solid var(--border); color: var(--muted); font-size: .7rem; }}
    .job-status[data-status="completed"] {{ color: var(--success); border-color: var(--success); }}
    .job-status[data-status="failed"] {{ color: var(--danger); border-color: var(--danger); }}
    .quiet {{ color: var(--muted); font-size: .8rem; }}
    .toast-container {{ position: fixed; right: 1rem; bottom: 1rem; z-index: 50; display: grid; gap: .5rem; }}
    .toast {{ max-width: 24rem; padding: .7rem .9rem; border: 1px solid var(--border); border-left: 4px solid var(--accent); background: var(--surface); box-shadow: 0 6px 20px rgba(0,0,0,.14); font-size: .82rem; }}
    .toast-success {{ border-left-color: var(--success); }}
    .toast-error {{ border-left-color: var(--danger); }}
    @media (max-width: 620px) {{
      .task-bar-inner {{ width: min(100% - 1.25rem, 66rem); }}
      .brand span, .version {{ display: none; }}
      .workspace {{ width: 100%; margin: 0; border-width: 0; box-shadow: none; }}
      .page-head {{ padding: 1.35rem 1rem .85rem; }}
      .form-body {{ padding: 1.1rem 1rem 1.5rem; }}
      .source-tab {{ flex: 1; min-width: 0; padding: .55rem .35rem; font-size: .82rem; }}
      .source-panel {{ min-height: 0; }}
      .file-options {{ grid-template-columns: 1fr; gap: .8rem; }}
      .activity {{ margin: 0 1rem 1.5rem; }}
      .form-actions {{ align-items: stretch; flex-direction: column-reverse; }}
      .primary-actions {{ display: grid; gap: .55rem; margin: 0; }}
      .btn {{ width: 100%; }}
    }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ transition: none !important; }} }}
  </style>
</head>
<body>
  <a class="skip-link" href="#ingest-form">Skip to form</a>
  <header class="task-bar">
    <div class="task-bar-inner">
      <a class="brand" href="/"><strong>TextStrata</strong><span>Local knowledge workspace</span></a>
      <div class="bar-actions"><span class="version" data-version-badge data-version-source="/whoami">{version}</span><a class="btn" href="/">Back to library</a></div>
    </div>
  </header>
  <main class="workspace" id="main">
    <header class="page-head">
      <h1>New Note</h1>
      <p>Choose one source. Only the options needed for that source are shown.</p>
    </header>
    <form id="ingest-form" class="form-body" enctype="multipart/form-data" novalidate>
      <div class="source-tabs" role="tablist" aria-label="Knowledge source">
        <button id="tab-url" class="source-tab" type="button" role="tab" aria-controls="content-url" aria-selected="true" data-source="url">Web link</button>
        <button id="tab-file" class="source-tab" type="button" role="tab" aria-controls="content-file" aria-selected="false" tabindex="-1" data-source="file">File upload</button>
        <button id="tab-text" class="source-tab" type="button" role="tab" aria-controls="content-text" aria-selected="false" tabindex="-1" data-source="text">Blank text</button>
      </div>

      <section id="content-url" class="source-panel" role="tabpanel" aria-labelledby="tab-url" data-source-panel="url">
        <div class="field">
          <label for="ingest-url">URL or YouTube channel</label>
          <input id="ingest-url" name="url" type="text" inputmode="url" autocomplete="url" placeholder="https://example.com/article or @channel">
          <span id="detect-hint" class="hint" aria-live="polite"></span>
        </div>
      </section>

      <section id="content-file" class="source-panel" role="tabpanel" aria-labelledby="tab-file" data-source-panel="file" hidden>
        <div class="drop-shell">
          <div id="drop-zone" class="drop-zone">
            <div>
              <strong>Drop supported files here</strong>
              <p>PDF, DOCX, PPTX, XLSX, HTML, images, audio, Markdown, and text up to 64 MiB each</p>
              <input id="ingest-file" name="file" type="file" multiple>
              <button id="browse-files" class="btn" type="button">Choose files</button>
            </div>
          </div>
          <div id="selected-files" class="selected-files" aria-live="polite">No files selected.</div>
          <div class="file-options">
            <div class="field">
              <label for="ocr-mode">Image extraction</label>
              <select id="ocr-mode" name="ocr_mode"><option value="both">Image + OCR text</option><option value="image">Image only</option><option value="text">OCR text only</option></select>
            </div>
            <div class="check-field"><input id="keep-original" name="keep_original" type="checkbox" value="true"><label for="keep-original">Retain original uploaded file</label></div>
          </div>
        </div>
      </section>

      <section id="content-text" class="source-panel" role="tabpanel" aria-labelledby="tab-text" data-source-panel="text" hidden>
        <div class="field">
          <label for="ingest-content">Markdown or text</label>
          <textarea id="ingest-content" name="content" placeholder="# New knowledge item"></textarea>
          <span class="hint">Paste or drop images into the editor to upload and embed them.</span>
        </div>
      </section>

      <section class="metadata" aria-labelledby="metadata-heading">
        <h2 id="metadata-heading">Optional details</h2>
        <div class="field">
          <label for="ingest-title">Title or filename <span class="optional">Optional</span></label>
          <input id="ingest-title" name="title" type="text" autocomplete="off" placeholder="Used when content has no explicit title">
          <span id="title-hint" class="hint">Used only when the source has no explicit title.</span>
        </div>
        <div class="field">
          <label for="acquire-notes">Acquisition notes <span class="optional">Optional</span></label>
          <textarea id="acquire-notes" name="notes" rows="3" placeholder="Why this source is being saved"></textarea>
          <span id="acquire-notes-hint" class="hint">Stored with the imported source for future context.</span>
        </div>
      </section>

      <div class="form-actions">
        <a class="btn" href="/?open=imports">Open import history</a>
        <div class="primary-actions">
          <span id="ingest-status" class="status" role="status" aria-live="polite"></span>
          <button id="ingest-submit" class="btn btn-primary" type="submit">Import link</button>
        </div>
      </div>
    </form>
    <details class="activity">
      <summary>Recent import activity</summary>
      <div class="activity-head"><span id="queue-status" class="hint"></span><button id="queue-refresh" class="btn" type="button">Refresh</button></div>
      <div id="ingest-queue" class="queue" aria-live="polite"><p class="quiet">Loading recent imports...</p></div>
    </details>
  </main>
  <div id="toast-container" class="toast-container" aria-live="polite"></div>
{client_asset_tag("new-note", version)}
</body>
</html>"""
