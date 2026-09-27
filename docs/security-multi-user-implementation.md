# TextStrata Security and Multi-User Implementation

Status: in progress on `security/multi-user-foundation`. Gate 1 and gate 2 are
implemented and have passed independent review. Do not deploy this branch as an
office service yet. The current household service has not been migrated.

## Product contract

One install serves an invited household or a 25-35 person IT team. Keep one
repository and deliver reviewed increments on a dedicated worktree. Public
signup, unrelated customer tenants, anonymous sharing, and simultaneous editing
are outside the initial release.

Installations choose their own workspace, network address, port, public URL,
and TLS method. No service port, hostname, subnet, certificate authority, or
host-specific directory is a product requirement. The packaged setup must work
without the developer machine's portbroker. Defaults may be conservative, but
network and storage choices must be easy to inspect and change later.

Network modes to deliver:

| Mode | Access | Requirements |
| --- | --- | --- |
| Personal HTTP | Loopback only | Default; no LAN exposure. |
| Direct HTTPS | Installer-selected LAN address | Built-in TLS with supplied certificate/key or guided local certificate, plus application authentication. |
| Reverse-proxy HTTPS | Installer-selected private backend | Application authentication still required; documented trusted-proxy and public URL settings. |

Reject non-loopback HTTP without authentication. Never infer a secure mode from a
host address or from a proxy-supplied identity header. Local native Linux and a
container deployment usable on Linux, Windows, and macOS are the first supported
service paths. Native Windows/macOS service support follows platform locking and
service tests.

## Identity and access

Bootstrap the first installation administrator from a local command, not a
first-visitor web claim. Subsequent people join by expiring, single-use invite.
Use secure browser sessions, CSRF protection, login throttling, logout and
session revocation. Company OIDC and MFA follow before office rollout.

An installation Admin manages accounts and service configuration. Space Owner,
Editor, and Viewer govern content actions. Personal spaces start private; shared
spaces admit members or groups. Admin does not silently gain ordinary browsing
access to personal spaces. Every route, asset, search result, graph, queue,
revision, trash item, export, and worker publication must use the same actor,
space, action, and resource authorization boundary. Unknown identities or
permission state deny access. Editable Markdown and tags cannot grant rights.

Ordinary AI workers are individually enrolled and revocable with space/action
scopes. New workers submit proposals first; an administrator reviews real work
before promoting a runner to scoped direct writes. Claimed model provenance is
not an authenticated worker identity. Agents with direct filesystem access can
bypass API policy, so production runners must not mount writable content roots.

Keep Markdown authoritative and search catalogs rebuildable. Store identities,
memberships, resource ownership, token state, and migration history in durable
application metadata. Use per-space stores initially to reduce accidental
cross-space retrieval. A personal note can be promoted through an explicit
authorized transfer, not a filesystem rename.

Spaces control access. Existing tags become labels for topic organization.
Collections give folder-like navigation by referencing notes without changing
their location or permissions. A note may appear in multiple collections.

## Delivery gates

1. **Containment and portable setup (done, uncommitted):** block unauthenticated
   LAN binding, make reads/assets private, remove mutating GET behavior,
   constrain unsafe rendered URLs, and introduce versioned installation
   configuration.
2. **Authenticated LAN entry (reviewed):** add first-admin bootstrap, sessions, CSRF,
   invitation management, production HTTP serving, direct HTTPS and optional
   reverse proxy modes. Cover all read and write routes, including legacy aliases
   and assets. Only then permit LAN binding. Test direct-port and spoofed-header
   bypasses. Keep the current service untouched until verified.
3. **Shared authorization:** establish stable user/space IDs and centralized
   per-action checks used by HTTP, MCP, workers, search, exports, and backup
   operations. Add per-space stores, membership roles, and explicit migration of
   a copied legacy workspace to the first owner's personal space. Recheck
   authorization at queued job publication.
4. **Agents and organization:** add runner enrollment, revocation, proposal
   review and promotion; then space switcher, labels, collections, and saved
   searches. Organization changes must never affect permissions.
5. **Operations and pilot:** structured actor-aware audit logs, supervised and
   recoverable workers, versioned migrations, complete protected backups,
   historical upgrade tests, CI, and a 35-session mixed workload. Require an
   independent security review and demonstrated restore before office rollout.

For each gate, run anonymous and cross-role denial tests against both browser and
direct API paths. Also test aliases, asset downloads, cache headers, job races,
revocation, and failure recovery. A passing test count alone is not evidence of
authorization completeness.

## Current configuration interface

`textstrata setup` interactively chooses a workspace, bind address, and port;
`--mode`, `--auth`, `--tls-cert`, `--tls-key`, `--public-url`,
`--trusted-proxy`, and `--state-dir` select the network mode and its settings.
`--non-interactive` takes the supplied values and safe defaults. Changing mode
drops the previous mode's specific settings. The versioned
JSON lives beside the platform's existing global TOML, or at `TEXTSTRATA_CONFIG`
or `--config`. `textstrata config show|check` inspects it. Running setup again
changes the choices; restart the service to apply them. CLI workspace and
network environment overrides take precedence, but unsafe LAN overrides fail
closed. Existing global/workspace TOML remains supported.

The setup command currently initializes the selected workspace. Moving an
existing workspace is **not** a data migration: copy/verify its data separately
before changing the configured path. Neither setup nor a web setting should
silently move content. Future configuration UI must validate a new URL and
retain a recovery path before switching a live service.

## Gate 2 progress (2026-09-27)

Implemented, reviewed, and ready for release-branch preparation:

- `auth.py`: installation-level SQLite identity store (users, sessions,
  invitations, login failures). Passwords use stdlib `hashlib.scrypt`
  (N=2^17, r=8, p=1) with a stored parameter string, so costs can rise later.
  Session and invitation secrets come from `secrets`; only their SHA-256
  digests are stored. Sessions have a 12 hour idle and 7 day absolute limit.
  Password change and disable revoke every session. Five failures per account
  or twenty per address trigger an exponential lockout capped at 15 minutes.
  Unknown, wrong, and disabled accounts produce the same response and do the
  same hashing work.
- `web_auth.py` plus `Handler._admit`: a default-deny gate in front of every
  route, including legacy `/api/fabric` and `/static/fabric-` aliases, client
  assets, and uploaded assets. Public paths are `/login`, `/logout`,
  `/invite/<code>`, and `/healthz` from loopback or trusted proxies only. Writes
  need the session CSRF token in `X-CSRF-Token` and a matching `Origin` when
  one is sent. Rendered pages get a head fragment that adds the token to
  same-origin `fetch` writes and a File > Sign out item. Identity headers
  (`X-Remote-User`, `X-Forwarded-User`, `Authorization`) are never read.
  `X-Forwarded-For` is honored only from configured trusted proxies, and only
  for throttling addresses. Administrator-only routes: `/api/textstrata/admin/*`
  and every `*/restart`.
- Cookies are `HttpOnly; SameSite=Lax`. In `https` and `proxy` mode they are
  `__Host-` prefixed and `Secure`. Every response carries `nosniff`,
  `X-Frame-Options: DENY`, and `Referrer-Policy: same-origin`. Account modes add
  `Cache-Control: private, no-store` and `Vary: Cookie`, and `https` mode adds
  HSTS.
- Unauthenticated local mode now refuses non-loopback `Host` headers (421),
  which closes DNS rebinding against the personal server.
- Configuration: `network.mode` is `local`, `https`, or `proxy`, with an
  optional `state_dir`. `https` requires absolute TLS certificate and key
  paths, uses TLS 1.2 or newer, and performs the handshake on the worker
  thread. `proxy` requires an https `public_url` origin and a non-empty
  `trusted_proxies` list. Non-local modes force `auth: true`. The server and
  `restart` refuse to start an account mode that has no accounts, and
  `restart` validates before it stops the running server. Handler sockets time
  out after 60 seconds.
- CLI: `textstrata users bootstrap-admin|invite|list|disable|enable|revoke-sessions|set-password`.
  API: `GET /api/textstrata/session`, `GET /api/textstrata/admin/users`,
  `POST /api/textstrata/admin/invites`.
- Proxy contract: in `proxy` mode the backend answers only peers listed in
  `trusted_proxies` whose first `X-Forwarded-Proto` value is `https`. The
  proxy must terminate TLS and overwrite (not append to) `X-Forwarded-Proto`
  and `X-Forwarded-For`. Loopback `/healthz` is the only exception.
- Administrator-only routes are classified centrally in
  `web_auth.is_admin_path`: control (backup, restore, and their previews),
  setup, global settings writes, acquisition maintenance and sync, the parity
  gateway, permanent trash and queue purges, invitations, and restart.

Verification: `tests/test_auth.py` and `tests/test_web_auth.py` add 34 tests.
They cover an anonymous sweep of reads, writes, deletes, aliases, and assets;
spoofed identity headers; CSRF and Origin denial; another user's CSRF token;
member denial on admin routes; single-use invitations; logout revocation;
disable revocation; throttling; rebound Host names; and Node execution of the
injected fetch wrapper. They also run a CLI end-to-end test that sets up
`https` mode, is refused before bootstrap, and then logs in over real TLS
while plain HTTP is rejected. The full quality gate passed with 636 tests. A
manual Chrome check against a disposable instance confirmed the login
redirect, the settings save through the page's own fetch, the 403 for a
tokenless XHR, the Sign out menu, and revocation on logout.

Open items after gate 2:

1. Resolved by review: scrypt at N=2^17, r=8, p=1 matches the OWASP
   recommendation and stays.
2. The server is still stdlib `ThreadingHTTPServer`. It is now capped at 64
   concurrent workers, and password hashing is capped at two concurrent scrypt
   calls per process. There are still no request-rate limits beyond login, and
   a production server adapter remains in the delivery plan.
3. Guided local certificate generation for `https` mode is not built;
   operators supply a certificate and key.
4. There is no browser admin screen yet; the CLI and admin API cover
   invitations and account state.
5. Non-browser HTTP clients (`control.py`, automation) cannot authenticate yet.
   They need gate 4 runner tokens.
6. In `local` mode with `--auth`, the cookie cannot be `Secure`, and browsers
   share cookies across ports on 127.0.0.1, so another local web app could
   overwrite it. Use `https` mode wherever other people's apps share the host.
7. There is no Content-Security-Policy yet, because pages rely on inline
   scripts. Expired sessions and failure rows are not pruned.
8. Container and service-unit updates for the new modes are not done.
