"""Small deterministic Markdown subset used by TextStrata renderers."""

from __future__ import annotations

from html import escape
import math
import re


_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*(?:\n|$)", re.S)
_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WORD_RE = re.compile(r"\w+")


def inline_markdown(text: str, *, link_resolver: dict[str, tuple[str, str]] | None = None) -> str:
    escaped = escape(text)
    escaped = re.sub(
        r"!\[([^\]]*)\]\((/asset/[0-9a-f]{64}(?:\?preview=1)?)\)",
        lambda m: (
            f'<figure class="content-figure"><img class="content-image" data-lightbox="1" src="{m.group(2)}" alt="{m.group(1)}" loading="lazy" decoding="async">'
            f'<figcaption class="content-caption">{m.group(1)}</figcaption></figure>'
            if m.group(1).strip()
            else f'<img class="content-image" data-lightbox="1" src="{m.group(2)}" alt="" loading="lazy" decoding="async">'
        ),
        escaped,
    )
    if link_resolver is not None:
        def replace_wikilink(match: re.Match[str]) -> str:
            target = match.group(1).strip()
            label = (match.group(2) or "").strip()
            resolved = link_resolver.get(target.casefold())
            if resolved:
                item_id, title = resolved
                text_value = label or title
                return f'<a class="wikilink" href="/item/{escape(item_id, quote=True)}">{escape(text_value)}</a>'
            return f'<span class="wikilink-missing">{escape(match.group(0))}</span>'
        escaped = re.sub(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]", replace_wikilink, escaped)
    escaped = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", escaped)
    escaped = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{escape(m.group(2), quote=True)}">{m.group(1)}</a>',
        escaped,
    )
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"_([^_]+)_", r"<em>\1</em>", escaped)
    return escaped


def anchor(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return value or "section"


def format_dt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return value.replace("T", " ").replace("+00:00", " UTC")
    except Exception:
        return value


def timestamp_seconds(value: str) -> int:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return parts[0] * 60 + parts[1]


def calculate_read_time(markdown_text: str, wpm: int = 238) -> int:
    if not markdown_text or not markdown_text.strip():
        return 0
    if wpm <= 0:
        raise ValueError("wpm must be positive")
    text = _FRONTMATTER_RE.sub("", markdown_text, count=1)
    text = _FENCED_CODE_RE.sub("", text)
    text = _HTML_TAG_RE.sub(" ", text)
    words = len(_WORD_RE.findall(text))
    if words == 0:
        return 0
    return max(1, math.floor((words / wpm) + 0.5))


def markdown_to_html(markdown: str, source_url: str | None = None, *, link_resolver: dict[str, tuple[str, str]] | None = None) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    code_lines: list[str] = []
    code_lang = ""
    in_code = False
    in_timestamp_section = False

    render_inline = lambda value: inline_markdown(value, link_resolver=link_resolver)

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            out.append(f"<p>{render_inline(' '.join(paragraph).strip())}</p>")
            paragraph = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            out.append("<ul>" + "".join(f"<li>{render_inline(item)}</li>" for item in list_items) + "</ul>")
            list_items = []

    def flush_code() -> None:
        nonlocal code_lines, code_lang
        if code_lines:
            lang = f' class="language-{escape(code_lang, quote=True)}"' if code_lang else ""
            code_html = escape("\n".join(code_lines))
            out.append(f"<pre><code{lang}>{code_html}</code></pre>")
            code_lines = []
            code_lang = ""

    for raw in lines:
        line = raw.rstrip("\n")
        fence = re.match(r"^```([A-Za-z0-9_+-]*)\s*$", line)
        if fence:
            if in_code:
                flush_code()
                in_code = False
            else:
                flush_paragraph()
                flush_list()
                in_code = True
                code_lang = fence.group(1)
            continue
        if in_code:
            code_lines.append(raw)
            continue
        if in_timestamp_section and source_url:
            timestamp = re.match(r"^\[(\d{1,2}:\d{1,2}(?::\d{1,2})?)\]\s+(.+)$", line)
            if timestamp:
                flush_paragraph()
                flush_list()
                raw_value = timestamp.group(1)
                display_value = ":".join(f"{int(p):02d}" for p in raw_value.split(":"))
                separator = "&" if "?" in source_url else "?"
                href = f"{source_url}{separator}t={timestamp_seconds(raw_value)}s"
                out.append(f'<div class="transcript-row"><a class="transcript-time" href="{escape(href, quote=True)}" target="_blank" rel="noopener">{escape(display_value)}</a><div class="transcript-text">{render_inline(timestamp.group(2))}</div></div>')
                continue
            if line.strip() and not line.startswith("#"):
                in_timestamp_section = False
        if not line.strip():
            flush_paragraph()
            flush_list()
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            heading_text = heading.group(2).strip()
            out.append(f'<h{level} id="{escape(anchor(heading_text), quote=True)}">{render_inline(heading_text)}</h{level}>')
            in_timestamp_section = heading_text.lower() == "timestamped transcript"
            continue
        bullet = re.match(r"^[-*]\s+(.+)$", line)
        if bullet:
            flush_paragraph()
            list_items.append(bullet.group(1).strip())
            continue
        if list_items:
            flush_list()
        paragraph.append(line.strip())

    if in_code:
        flush_code()
    flush_paragraph()
    flush_list()
    return "\n".join(out)


def generate_toc(markdown: str) -> str | None:
    headings: list[tuple[int, str, str]] = []
    for line in markdown.splitlines():
        m = re.match(r"^(#{2,3})\s+(.+)$", line.strip())
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            headings.append((level, text, anchor(text)))
    if len(headings) < 2:
        return None
    items: list[str] = []
    for level, text, heading_anchor in headings:
        pad = ' style="padding-left:1.2rem"' if level == 3 else ""
        items.append(f'<li{pad}><a href="#{escape(heading_anchor, quote=True)}">{escape(text)}</a></li>')
    return f"""<div class="toc"><div class="toc-head">Contents</div><ul>{"".join(items)}</ul></div>"""
