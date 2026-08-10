"""Presentation skins and persisted appearance preference mapping."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Skin:
    name: str
    page_title: str
    background: str
    surface: str
    surface_alt: str
    text: str
    muted: str
    accent: str
    accent_soft: str
    border: str
    warning: str
    success: str
    danger: str
    radius: str = "6px"
    font_body: str = "Iowan Old Style, Palatino, Georgia, serif"
    font_ui: str = "system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    font_mono: str = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    max_width: str = "76rem"
    font_scale: str = "100%"
    spacing: str = "1rem"
    card_shadow: str = "0 18px 40px rgba(0,0,0,0.04)"
    card_border: str = "1px solid var(--border)"
    motion_duration: str = "180ms"


PAPER_SKIN = Skin(
    name="paper",
    page_title="TextStrata",
    background="#f4f5f6",
    surface="#ffffff",
    surface_alt="#eef1f3",
    text="#202428",
    muted="#5e6872",
    accent="#0f766e",
    accent_soft="#d9f4ef",
    border="#d7dde2",
    warning="#a16207",
    success="#166534",
    danger="#b91c1c",
)

CONSOLE_SKIN = Skin(
    name="console",
    page_title="TextStrata",
    background="#09111f",
    surface="#111827",
    surface_alt="#0f172a",
    text="#e5eef9",
    muted="#93a4b8",
    accent="#7dd3fc",
    accent_soft="#082f49",
    border="#253247",
    warning="#fbbf24",
    success="#34d399",
    danger="#fb7185",
    font_body="Inter, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
)

WIKIPEDIA_SKIN = Skin(
    name="wiki",
    page_title="TextStrata",
    background="#f8f9fa",
    surface="#ffffff",
    surface_alt="#f8f9fa",
    text="#202122",
    muted="#6c757d",
    accent="#3366cc",
    accent_soft="#eaf3ff",
    border="#a2a9b1",
    warning="#856404",
    success="#28a745",
    danger="#cc0000",
    radius="0px",
    font_body="system-ui, -apple-system, 'Segoe UI', Roboto, Lato, Helvetica, Arial, sans-serif",
    font_ui="system-ui, -apple-system, 'Segoe UI', Roboto, Lato, Helvetica, Arial, sans-serif",
    font_mono="ui-monospace, 'SFMono-Regular', 'Fira Code', 'Fira Mono', Menlo, Consolas, monospace",
    max_width="84rem",
    card_shadow="none",
    card_border="1px solid var(--border)",
)

ACCENTS = {
    "teal": ("#0f766e", "#d9f4ef"),
    "blue": ("#1d4ed8", "#dbeafe"),
    "plum": ("#7e22ce", "#f3e8ff"),
    "amber": ("#a16207", "#fef3c7"),
}


def skin_from_settings(settings: dict | None) -> Skin:
    prefs = (settings or {}).get("presentation", settings or {})
    skin_name = str(prefs.get("skin", "paper"))
    base = (
        CONSOLE_SKIN
        if skin_name == "console"
        else WIKIPEDIA_SKIN
        if skin_name == "wiki"
        else PAPER_SKIN
    )
    accent, accent_soft = ACCENTS.get(str(prefs.get("accent", "teal")), ACCENTS["teal"])
    if base is CONSOLE_SKIN:
        accent_soft = {
            "teal": "#083b3a",
            "blue": "#172554",
            "plum": "#3b0764",
            "amber": "#451a03",
        }.get(str(prefs.get("accent")), "#083b3a")
    density = {"compact": ".72rem", "comfortable": "1rem", "spacious": "1.3rem"}.get(
        str(prefs.get("density")), "1rem"
    )
    width = {"focused": "62rem", "wide": "76rem", "fluid": "96rem"}.get(
        str(prefs.get("content_width")), "76rem"
    )
    style = str(prefs.get("card_style", "soft"))
    shadow = "none" if style != "soft" else base.card_shadow
    border = "1px solid transparent" if style == "flat" else "1px solid var(--border)"
    scale = min(120, max(90, int(prefs.get("font_scale", 100))))
    motion = "0ms" if str(prefs.get("motion", "system")) == "reduced" else base.motion_duration
    return replace(
        base,
        accent=accent,
        accent_soft=accent_soft,
        spacing=density,
        max_width=width,
        card_shadow=shadow,
        card_border=border,
        font_scale=f"{scale}%",
        motion_duration=motion,
    )


def skin_vars(skin: Skin) -> str:
    return f"""--bg:{skin.background};--surface:{skin.surface};--surface-alt:{skin.surface_alt};--text:{skin.text};--muted:{skin.muted};--accent:{skin.accent};--accent-soft:{skin.accent_soft};--border:{skin.border};--warning:{skin.warning};--success:{skin.success};--danger:{skin.danger};--radius:{skin.radius};--font-body:{skin.font_body};--font-ui:{skin.font_ui};--font-mono:{skin.font_mono};--max-width:{skin.max_width};--font-scale:{skin.font_scale};--space:{skin.spacing};--card-shadow:{skin.card_shadow};--card-border:{skin.card_border};--motion-duration:{skin.motion_duration};"""
