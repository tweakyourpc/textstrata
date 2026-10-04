"""HTTP-facing authentication policy for authenticated installation modes.

The route handlers in :mod:`textstrata.web` stay unaware of identity: the
request handler admits or refuses each request here before any route runs.
"""

from __future__ import annotations

import ipaddress
import json
import re
import ssl
from dataclasses import dataclass, field
from html import escape
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from .auth import SESSION_ABSOLUTE_SECONDS, IdentityStore
from .workspace import is_loopback_host

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
IDENTITY_FILENAME = "identity.sqlite3"
MAX_FORM_BYTES = 16 * 1024
_INVITE_RE = re.compile(r"^/invite/([A-Za-z0-9_-]{20,128})$")


@dataclass
class WebSecurity:
    identity: IdentityStore
    mode: str
    secure_cookies: bool
    public_url: str | None = None
    trusted_proxies: list[Any] = field(default_factory=list)

    @property
    def cookie_name(self) -> str:
        # The __Host- prefix makes browsers refuse the cookie unless it is
        # Secure, host-only, and Path=/, which blocks sibling-subdomain fixation.
        return "__Host-textstrata_session" if self.secure_cookies else "textstrata_session"

    def session_cookie(self, token: str) -> str:
        parts = [f"{self.cookie_name}={token}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={SESSION_ABSOLUTE_SECONDS}"]
        if self.secure_cookies:
            parts.append("Secure")
        return "; ".join(parts)

    def clear_cookie(self) -> str:
        parts = [f"{self.cookie_name}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
        if self.secure_cookies:
            parts.append("Secure")
        return "; ".join(parts)

    def session_token(self, cookie_header: str | None) -> str:
        if not cookie_header:
            return ""
        jar = SimpleCookie()
        try:
            jar.load(cookie_header)
        except CookieError:
            return ""
        morsel = jar.get(self.cookie_name)
        return morsel.value if morsel else ""

    def is_trusted_proxy(self, address: str) -> bool:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(ip in network for network in self.trusted_proxies)

    def client_address(self, peer: str, forwarded_for: str | None) -> str:
        """Return the originating address, honoring X-Forwarded-For only from trusted proxies."""
        if self.mode != "proxy" or not forwarded_for or not self.is_trusted_proxy(peer):
            return peer
        for hop in reversed([part.strip() for part in forwarded_for.split(",") if part.strip()]):
            if not self.is_trusted_proxy(hop):
                try:
                    return str(ipaddress.ip_address(hop))
                except ValueError:
                    return peer
        return peer

    def origin_allowed(self, origin: str | None, host: str | None) -> bool:
        """Browser Origin must match the public origin or the requested Host."""
        if not origin:
            return True
        parsed = urlsplit(origin)
        if self.public_url and f"{parsed.scheme}://{parsed.netloc}" == self.public_url:
            return True
        return bool(host) and parsed.netloc == host


def build_security(network: Mapping[str, Any], state_dir: Path, **identity_options: Any) -> WebSecurity | None:
    if not network.get("auth"):
        return None
    mode = network["mode"]
    return WebSecurity(
        identity=IdentityStore(state_dir / IDENTITY_FILENAME, **identity_options),
        mode=mode,
        secure_cookies=mode in {"https", "proxy"},
        public_url=network.get("public_url"),
        trusted_proxies=[ipaddress.ip_network(item, strict=False) for item in network.get("trusted_proxies", [])],
    )


def tls_context(network: Mapping[str, Any]) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(network["tls"]["cert_file"], network["tls"]["key_file"])
    return context


def local_host_header_allowed(host_header: str | None) -> bool:
    """Reject DNS-rebinding requests against unauthenticated loopback servers."""
    if not host_header:
        return False
    try:
        hostname = urlsplit(f"//{host_header}").hostname
    except ValueError:
        return False
    return bool(hostname) and is_loopback_host(hostname)


def is_public_path(path: str) -> bool:
    return path in {"/login", "/logout"} or bool(_INVITE_RE.fullmatch(path))


def invite_token(path: str) -> str | None:
    match = _INVITE_RE.fullmatch(path)
    return match.group(1) if match else None


# Installation operations, as opposed to shared content editing. Paths are the
# canonical form produced by web._canonical_path, so legacy /api/fabric aliases
# are covered. Classification is by path for every method: an operator GET can
# disclose server paths or configuration as readily as a POST can change them.
_ADMIN_PREFIXES = (
    "/api/textstrata/admin/",
    "/api/textstrata/control/",
    "/api/textstrata/setup/",
    "/api/acquisition/maintenance/",
    "/api/parity/",
    "/api/acquisition/channel/",
)
_ADMIN_EXACT = frozenset({
    "/setup",
    "/api/textstrata/restart",
    "/api/acquisition/sync",
    "/api/acquisition/trash/empty",
    "/api/acquisition/queue/clear-completed",
    "/api/textstrata/trash/empty",
})
# Global presentation and retention settings: everyone reads them to render
# pages, only administrators change them.
_ADMIN_WRITES = frozenset({"/api/textstrata/settings"})


def is_admin_path(path: str, method: str = "GET") -> bool:
    if path in _ADMIN_EXACT or path.startswith(_ADMIN_PREFIXES) or path.endswith("/restart"):
        return True
    if method not in SAFE_METHODS and path in _ADMIN_WRITES:
        return True
    if path.startswith("/api/acquisition/queue/") and path.endswith("/purge-output"):
        return True
    # Permanent deletion of shared trash; moving items to and from trash stays a member action.
    return method == "DELETE" and path.startswith(("/api/textstrata/trash/", "/api/acquisition/queue/"))


def proxy_ingress_allowed(security: WebSecurity, peer: str, forwarded_proto: str | None) -> bool:
    """In proxy mode, only configured proxies that terminated HTTPS may reach the application."""
    if security.mode != "proxy":
        return True
    if not security.is_trusted_proxy(peer):
        return False
    first = (forwarded_proto or "").split(",", 1)[0].strip().lower()
    return first == "https"


# Media types a browser renders without executing script. Everything else is
# served as a download under a sandbox policy so that uploaded HTML, SVG, XML,
# or scripts cannot run in the application origin.
_INLINE_MEDIA = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp",
    "text/plain",
})
_INLINE_MEDIA_PREFIXES = ("audio/", "video/")
_MEDIA_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$")
ASSET_CSP = "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; sandbox"


def asset_response_headers(media_type: str, asset_id: str) -> list[tuple[str, str]]:
    """Headers for serving a stored upload, derived from its recorded media type."""
    media = media_type.split(";", 1)[0].strip().lower()
    if not _MEDIA_TYPE_RE.fullmatch(media):
        media = "application/octet-stream"
    inline = media in _INLINE_MEDIA or (media.startswith(_INLINE_MEDIA_PREFIXES) and "xml" not in media)
    headers = [("Content-Security-Policy", ASSET_CSP), ("X-Content-Type-Options", "nosniff")]
    if inline:
        content_type = "text/plain; charset=utf-8" if media == "text/plain" else media
        return [("Content-Type", content_type), ("Content-Disposition", "inline"), *headers]
    safe_id = re.sub(r"[^0-9a-f]", "", asset_id)[:64] or "asset"
    # SVG keeps its type so <img> embeds still render; direct navigation downloads it.
    content_type = media if media == "image/svg+xml" else "application/octet-stream"
    return [("Content-Type", content_type), ("Content-Disposition", f'attachment; filename="asset-{safe_id}"'), *headers]


def safe_next(value: str | None) -> str:
    """Only same-site absolute paths are valid post-login destinations."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(char) < 32 for char in value):
        return "/"
    return value


def parse_form(raw: bytes) -> dict[str, str]:
    values = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True, max_num_fields=10)
    return {key: items[0] for key, items in values.items()}


def _script_string(value: str) -> str:
    """JSON string literal that cannot terminate an inline <script> element."""
    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def session_bootstrap(csrf_token: str, username: str) -> str:
    """Head fragment: attach the CSRF token to same-origin writes and offer sign-out."""
    token = _script_string(csrf_token)
    label = _script_string(f"Sign out ({username})")
    return (
        '<meta name="textstrata-csrf" content="' + escape(csrf_token, quote=True) + '">'
        "<script>(()=>{const t=" + token + ";const f=window.fetch.bind(window);"
        "window.fetch=(input,init)=>{const o=Object.assign({},init||{});"
        "const r=input instanceof Request?input:null;"
        "const m=String(o.method||(r?r.method:'GET')).toUpperCase();"
        "const u=new URL(r?r.url:String(input),location.href);"
        "if(u.origin===location.origin&&!['GET','HEAD','OPTIONS'].includes(m)){"
        "const h=new Headers(o.headers||(r?r.headers:undefined));h.set('X-CSRF-Token',t);o.headers=h}"
        "return f(input,o)};"
        "document.addEventListener('DOMContentLoaded',()=>{const menu=document.getElementById('file-menu');if(!menu)return;"
        "const b=document.createElement('button');b.setAttribute('role','menuitem');b.textContent=" + label + ";"
        "b.addEventListener('click',()=>{location.href='/logout'});"
        "menu.appendChild(document.createElement('hr'));menu.appendChild(b)})})();</script>"
    )


def inject_head(body: str, fragment: str) -> str:
    match = re.search(r"<head[^>]*>", body, re.I)
    if not match:
        return fragment + body
    return body[:match.end()] + fragment + body[match.end():]


_PAGE_STYLE = (
    "body{font-family:system-ui,sans-serif;background:#f6f5f2;color:#1d1d1b;margin:0;display:grid;"
    "place-items:center;min-height:100vh;padding:16px;box-sizing:border-box}"
    "main{background:#fff;border:1px solid #d9d6cf;border-radius:8px;padding:28px;width:100%;max-width:360px}"
    "h1{font-size:1.25rem;margin:0 0 16px}label{display:block;font-size:.9rem;margin:12px 0 4px}"
    "input{width:100%;box-sizing:border-box;padding:8px;border:1px solid #bbb;border-radius:4px;font:inherit}"
    "button{margin-top:18px;width:100%;padding:9px;border:0;border-radius:4px;background:#1d1d1b;color:#fff;font:inherit;cursor:pointer}"
    ".error{background:#fbe9e7;border:1px solid #e0b4ad;padding:8px;border-radius:4px;font-size:.9rem}"
    ".note{color:#555;font-size:.85rem}"
    "@media (prefers-color-scheme:dark){body{background:#161615;color:#eee}main{background:#222;border-color:#3a3a38}"
    "input{background:#1a1a19;color:#eee;border-color:#555}button{background:#eee;color:#111}"
    ".error{background:#3b1f1b;border-color:#6b3a33}.note{color:#aaa}}"
)


def _page(title: str, inner: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)} · TextStrata</title><style>{_PAGE_STYLE}</style></head>"
        f"<body><main>{inner}</main></body></html>"
    )


def _error(message: str | None) -> str:
    return f'<p class="error" role="alert">{escape(message)}</p>' if message else ""


def login_page(next_path: str, error: str | None = None, username: str = "") -> str:
    return _page("Sign in", (
        "<h1>Sign in to TextStrata</h1>" + _error(error)
        + '<form method="post" action="/login">'
        + f'<input type="hidden" name="next" value="{escape(safe_next(next_path), quote=True)}">'
        + '<label for="username">Username</label>'
        + f'<input id="username" name="username" autocomplete="username" required value="{escape(username, quote=True)}">'
        + '<label for="password">Password</label>'
        + '<input id="password" name="password" type="password" autocomplete="current-password" required>'
        + '<button type="submit">Sign in</button></form>'
    ))


def logout_page(csrf_token: str | None) -> str:
    if not csrf_token:
        return _page("Signed out", '<h1>You are signed out</h1><p><a href="/login">Sign in</a></p>')
    return _page("Sign out", (
        "<h1>Sign out of TextStrata?</h1>"
        + '<form method="post" action="/logout">'
        + f'<input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">'
        + '<button type="submit">Sign out</button></form><p class="note"><a href="/">Back to the library</a></p>'
    ))


def invite_page(token: str, error: str | None = None, username: str = "") -> str:
    return _page("Accept invitation", (
        "<h1>Create your TextStrata account</h1>" + _error(error)
        + f'<form method="post" action="/invite/{escape(token, quote=True)}">'
        + '<label for="username">Username</label>'
        + f'<input id="username" name="username" autocomplete="username" required value="{escape(username, quote=True)}">'
        + '<label for="password">Password</label>'
        + '<input id="password" name="password" type="password" autocomplete="new-password" minlength="12" required>'
        + '<label for="confirm">Confirm password</label>'
        + '<input id="confirm" name="confirm" type="password" autocomplete="new-password" minlength="12" required>'
        + '<p class="note">Use at least 12 characters. A passphrase works well.</p>'
        + '<button type="submit">Create account</button></form>'
    ))


def invite_closed_page() -> str:
    return _page("Invitation unavailable", (
        "<h1>Invitation unavailable</h1><p>This invitation link is invalid, expired, or already used. "
        "Ask your administrator for a new one.</p>"
    ))
