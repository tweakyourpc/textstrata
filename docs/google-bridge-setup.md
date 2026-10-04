# TextStrata Google Apps Script bridge: production setup

The bridge uses Apps Script's Google authorization. TextStrata authenticates to the web app with a signed request. The Inbox stages Google Docs for ingestion; the separate Library holds readable mirrors of locally canonical TextStrata articles. The Library is never queried as an Inbox source. A Library row explicitly marked `Updated` is a separate, guarded edit request.

## What came from markbase-control

The existing `markbase-control` project uses a short-lived timer worker, private config outside Git, narrow queue/completion actions, receipt-based state, and `LockService` around Sheet edits. Those patterns are used here. Its current web app has optional plain shared tokens and does not implement HMAC, timestamp/nonce replay defense, constant-time signature checks, connection bundles, or key rotation. This bridge adds one versioned signing format, shared by the Python client and Apps Script server, because there was no signed format to reuse.

## One-time Google setup

Create an Apps Script project in your Google account and prepare two separate folders: a TextStrata Inbox and a TextStrata Library. Keep your existing spreadsheet with the `Manifest` worksheet. Add a separate worksheet named `Library` in that spreadsheet, with this header row:

```text
ID | Title | Doc ID | Hash | Updated | Topic | Tags | Source | Status
```

The existing `Manifest` header row is:

```text
ID | Title | Created | Updated | Topic | Tags | Status | Doc ID | Source | Ingested | Hash
```

Each `Doc ID` in a Ready or Updated manifest row must belong to the Inbox folder. A mirrored Library Doc must reside in the Library folder. Use different folder IDs; the bridge enforces this for source fetches.

In the Apps Script project settings, add these Script Properties with your private values:

| Property | Value |
|---|---|
| `PROTOCOL_VERSION` | `1` |
| `BRIDGE_SECRET` | A strong random shared secret |
| `BRIDGE_SECRET_PREVIOUS` | Optional prior secret during rotation; remove after clients switch |
| `MANIFEST_SPREADSHEET_ID` | Your spreadsheet ID |
| `MANIFEST_SHEET` | `Manifest` |
| `INBOX_FOLDER_ID` | Your Inbox folder ID |
| `LIBRARY_FOLDER_ID` | Your Library folder ID |
| `LIBRARY_INDEX_SHEET` | `Library` |
| `ALLOWED_CLOCK_SKEW_SECONDS` | `120` |
| `NONCE_TTL_SECONDS` | `300` |

Keep the property values out of Git. A secret can be generated with `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`. Set the same value in the private `TEXTSTRATA_GOOGLE_BRIDGE_SECRET` environment variable used by TextStrata.

## Push and deploy with clasp

From `apps-script/google-bridge`, use your existing Apps Script project ID in the ignored `.clasp.json`:

```bash
cp .clasp.example.json .clasp.json
# Edit .clasp.json: replace PASTE_YOUR_OWN_APPS_SCRIPT_ID with your script ID.
clasp push
clasp create-deployment --description "TextStrata bridge v1"
```

If you do not have a project yet, run `clasp create-script --title "TextStrata Google Bridge" --type standalone` in that directory instead of copying the example `.clasp.json`; clasp will create the private file. For an existing deployment, use `clasp list-deployments` to find its deployment ID, then `clasp create-deployment --deploymentId YOUR_DEPLOYMENT_ID --description "TextStrata bridge v1"`. These are clasp 3.x commands. In clasp 2.x, `clasp create`, `clasp deployments`, and `clasp deploy` are the corresponding names.

Deploy as a Web App with `Execute as: Me` and `Who has access: Anyone`. Authorize its requested Sheets, Docs, and Drive permissions in the Google deployment flow. Copy the resulting `/exec` URL. The bridge needs Drive permission to verify folder membership and create/move Library Docs. Do not paste the URL or secret into tracked files.

## Configure private TextStrata

The committed `config/sources.example.yaml` contains the bridge endpoint template. Copy it to an ignored `config/sources.yaml` or set `TEXTSTRATA_SOURCES_CONFIG` to a private path. The bridge section is:

```yaml
google_bridge:
  endpoint: "${TEXTSTRATA_GOOGLE_BRIDGE_URL}"
```

Set these variables in your private shell or service environment:

```bash
export TEXTSTRATA_SOURCES_CONFIG=/private/textstrata/sources.yaml
export TEXTSTRATA_GOOGLE_BRIDGE_URL='https://script.google.com/macros/s/YOUR_DEPLOYMENT_ID/exec'
export TEXTSTRATA_GOOGLE_BRIDGE_SECRET='YOUR_PRIVATE_RANDOM_SECRET'
export TEXTSTRATA_WORKSPACE=/private/textstrata/knowledge
export TEXTSTRATA_STATE_DIR=/private/textstrata/state
```

For cron, put those variables in a private file such as
`~/.config/textstrata/google-bridge.env` with mode `0600`. Set
`TEXTSTRATA_BIN` there to the absolute path of the installed `textstrata`
executable. Use a private `sources.yaml` containing only the `google_bridge`
endpoint template above. A cron process does not inherit interactive shell
exports.

The bridge does not use local Google OAuth client credentials or a Google token. You can keep the direct OAuth adapter configured separately. Do not place secrets in YAML, the manifest, the Library index, or an environment file tracked by Git.

Run the connection and dry-run checks:

```bash
textstrata sources health google-bridge
textstrata ingest google-bridge --dry-run
textstrata ingest google-bridge
textstrata mirror google-bridge --dry-run
textstrata mirror google-bridge
```

The first ingest reports `NEW + ACK` for eligible articles and the bridge changes only their `Status`, `Ingested`, and `Hash` cells. A later Ready row whose content is already stored locally reports `UNCHANGED + ACK` and retries acknowledgment. The mirror compares canonical Markdown hashes with the Library index and reports `CREATE`, `UPDATE`, or `UNCHANGED`. It mirrors the useful article corpus and metadata; it is not a byte-for-byte application backup.

## Editing an existing Library article from Google Drive

Edit the existing Library Google Doc. Keep its `TextStrata ID`, `Origin: textstrata-library`, and canonical Markdown frontmatter intact; in particular, do not change the frontmatter `id` or provenance. Edit Title, Topic, and Tags in the **Library** index row; that row is authoritative for those fields. Leave `ID`, `Doc ID`, `Hash`, and `Source` unchanged. Set only that Library row's `Status` to `Updated` after all Doc and row edits are saved. Do not change the Inbox `Manifest` for an existing Library article.

On the next scheduled mirror pass, TextStrata reads only `Updated` Library rows, checks the indexed Doc still belongs to the Library folder, and compares the local article to the Library row's last-mirrored hash. If the local article is unchanged, the revision is imported, the canonical local article is re-mirrored, and the row returns to `Active`. If local and Google both changed, the row becomes `Conflict`; neither copy is overwritten. Resolve a conflict deliberately before setting it back to `Updated`. Dry run reports `IMPORT`, `RETRY`, or `CONFLICT` without changing local files or Google. A failed final Google acknowledgment is retried without re-publishing the same local content.

ChatGPT-facing instruction to place in the Google editing workflow:

> When updating an existing TextStrata Library item, edit its Library Google Doc and the matching Library index row. Keep the Doc's TextStrata ID, Origin line, canonical Markdown frontmatter ID, and provenance. Put title, topic, and tags changes in the Library row; do not change ID, Doc ID, Hash, or Source. After saving, set that Library row's Status to Updated. Do not edit the Inbox Manifest for this item. If the row becomes Conflict, stop and report the conflict; do not overwrite either copy.

## Protocol and operational boundary

Each POST body contains `version`, `action`, `timestamp` (UTC Unix seconds), a 32-character hex `nonce`, `payload_json`, and a lowercase SHA-256 HMAC hex `signature`. The signing string is exactly:

```text
version\naction\ntimestamp\nnonce\npayload_json
```

The client serializes `payload_json` with sorted keys and compact separators. The server signs the exact transmitted `payload_json` string before parsing it, avoiding cross-language serialization drift. The server accepts the current secret and an optional previous secret during rotation. It rejects stale/future timestamps, reused nonces, malformed payloads, invalid signatures, and unknown actions. Nonces are stored in Apps Script `CacheService` under `LockService` for the configured TTL. The shared synthetic vector is in `tests/fixtures/google_bridge_vector.json`.

`fetch_source` accepts a manifest ID and expected Doc ID; the server resolves and checks the actual Doc ID from the configured manifest and verifies the file is in Inbox and not Library. `ack` rechecks the row under a lock and writes only three named cells. `mirror_upsert` accepts a TextStrata article ID and canonical content, but no destination Doc ID; the destination is resolved from the Library index and verified in the Library folder. `fetch_library_revision` can read only an `Updated` Library index row and its matching Doc; `complete_library_import` rechecks the row, Doc, old hash, and document fingerprint before returning it to `Active`. Ordinary mirroring refuses pending and conflicted Library rows. There is no delete, arbitrary Drive read, arbitrary Sheet range, sharing, or script execution action.

For a 15-minute production interval on Linux, use the included wrapper from
cron. It loads the private environment file, uses a state-directory lock to
prevent overlapping runs, ingests first, then always runs the Library import
and mirror pass. A failed Inbox row does not block independent Library work;
the wrapper still exits nonzero so cron monitoring can report the failed pass.
Create the private log directory before adding this entry:

```cron
*/15 * * * * /path/to/textstrata-impl/scripts/google-bridge-sync.sh >> /private/textstrata/logs/google-bridge.log 2>&1
```

The local workspace, state directory, environment file, and logs must be
persistent and outside the Git checkout. The mirror scans **all** normalized
articles in the configured local workspace, so the first Library pass can
create more Docs than the number of newly ingested Inbox articles. Inspect its
dry-run output before enabling the schedule. If the canonical workspace
contains development notes that must stay local, set the private
`TEXTSTRATA_MIRROR_EXCLUDE_IDS` environment variable to their comma-separated
canonical IDs. This filter applies to both dry-run and write passes.

## Production checklist

1. Identify the Inbox folder ID and move each source Doc into it.
2. Create or identify the separate Library folder ID.
3. Verify the `Manifest` worksheet and create the `Library` index worksheet.
4. Set the Script Properties listed above.
5. Run `clasp push`.
6. Deploy/update the Web App as the deploying user and copy its `/exec` URL.
7. Set the private TextStrata endpoint, secret, workspace, and state variables.
8. Run `textstrata sources health google-bridge`.
9. Run `textstrata ingest google-bridge --dry-run`.
10. Run `textstrata ingest google-bridge` for existing Ready/Updated articles.
11. Run `textstrata mirror google-bridge --dry-run`.
12. Run `textstrata mirror google-bridge` for the local corpus.
13. Inspect the Library Docs and their index rows in Drive.
14. Verify the source manifest acknowledgment fields and an unchanged retry.
15. Configure a 15-minute cron or systemd timer.
