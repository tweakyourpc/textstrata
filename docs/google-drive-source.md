# Google Drive source adapter

TextStrata reads a deliberately published Google Drive staging area through a Google Sheet manifest. Google Drive is a transport source; the local TextStrata workspace remains the database and the existing deterministic Markdown ingestion pipeline remains the publication boundary.

The adapter reads one worksheet and then fetches the Google Docs referenced by eligible rows. The minimum manifest columns are:

| Column | Required | Meaning |
|---|---:|---|
| `ID` | yes | Stable TextStrata source record ID |
| `Doc ID` | yes | Stable Google Docs API document ID |
| `Status` | yes | Rows matching `eligible_statuses` are read |
| `Title` | no | Article title override |
| `Created`, `Updated` | no | Source timestamps |
| `Topic`, `Tags` | no | Local metadata |
| `Ingested`, `Hash`, `Source` | no | Human or operational tracking fields |

Rows with missing required values are reported and skipped. A document access failure is reported for that record while other valid records continue. The adapter calculates SHA-256 over normalized text, so a repeated poll with the same meaningful content is `UNCHANGED`; changed content is `UPDATE`.

## Configuration

Copy [config/sources.example.yaml](../config/sources.example.yaml) to `config/sources.yaml`, or point `TEXTSTRATA_SOURCES_CONFIG` at a private file. Environment substitution is supported:

```yaml
google_drive:
  spreadsheet_id: "${TEXTSTRATA_GOOGLE_MANIFEST_ID}"
  worksheet: "Manifest"
  eligible_statuses: [Ready, Updated]
  writeback:
    enabled: false
    success_status: Ingested
```

`config/sources.yaml` is ignored by Git. The workspace stores adapter state in `.fabric/google-drive-state.json`; it contains IDs and hashes, never document contents. Mount the entire TextStrata workspace as persistent storage in Docker.

## Authentication and Google Cloud setup

The adapter uses installed application OAuth for a local or self-hosted user account. This is the appropriate default for a private Drive because the user authorizes access to their own files and no service account needs to be granted access to every staged document. A service account can be useful for unattended organization-owned automation, but it requires explicit folder sharing, separate key management, and more operational care.

1. Create or select a Google Cloud project.
2. Enable Google Sheets API and Google Docs API.
3. Configure the OAuth consent screen for the intended account or organization.
4. Create an OAuth client of type Desktop app and download its JSON outside the repository.
5. Set `TEXTSTRATA_GOOGLE_CREDENTIALS_PATH` to that file.
6. Install the optional dependency with `pip install -e '.[google-drive]'`.
7. Run `textstrata sources health google-drive` once. The first run opens the browser authorization flow; later runs refresh the token when possible.

The adapter requests read-only Docs access and the Sheets scope required for optional manifest acknowledgment. Credentials and refresh tokens default to the platform's TextStrata configuration directory and can be redirected with `TEXTSTRATA_GOOGLE_TOKEN_PATH`. Both files must remain outside Git and should be readable only by the service account running TextStrata. If an existing cached token lacks the Sheets scope, the command returns `AUTHENTICATION_SCOPE_MISSING`; set `TEXTSTRATA_GOOGLE_REAUTH=1` or remove the cached token and authorize again.

## Commands

```bash
textstrata sources add google-drive
TEXTSTRATA_GOOGLE_MANIFEST_ID=synthetic_manifest_id textstrata sources health google-drive
textstrata ingest google --dry-run
textstrata ingest google
```

`--dry-run` authenticates, reads eligible rows, fetches documents, computes hashes, and reports `NEW`, `UPDATE`, `UNCHANGED`, or `ERROR` without publishing Markdown or changing local state. The normal command updates the local workspace and, only when explicitly enabled, acknowledges the manifest. It never edits Google Docs.

To enable production acknowledgment, set `writeback.enabled: true`. After local publication succeeds, TextStrata persists local state and then re-reads the configured worksheet. It confirms the same `ID` and `Doc ID`, then updates only `Status`, `Ingested`, and `Hash`. A failed acknowledgment does not roll back local content; the next run retries it when the local hash matches and the manifest still says `Ready` or `Updated`. Dry-run reports combinations such as `NEW + ACK` and never writes Google Sheets.

For production, set `TEXTSTRATA_STATE_DIR` to a persistent directory outside the Git checkout, such as `~/.local/state/textstrata`. Without it, state remains backward-compatible at `.fabric/google-drive-state.json`.

Use the existing scheduler, cron, systemd service, or container scheduler to invoke the one-shot command. A minimal container arrangement mounts the workspace at `/data/textstrata-store`, mounts a private source configuration at `/run/secrets/sources.yaml`, and supplies `TEXTSTRATA_SOURCES_CONFIG=/run/secrets/sources.yaml` plus the credential and token paths through private mounts or environment variables. Do not bake any of these files into an image.

For a 15-minute cron schedule, export the private variables in the service user's environment and use:

```cron
*/15 * * * * cd /path/to/textstrata && /path/to/venv/bin/textstrata ingest google >> /private/textstrata/google-ingest.log 2>&1
```

## Privacy boundary

Normal logs contain source type, manifest record ID, action, result, and change status. They do not contain access tokens, client secrets, refresh tokens, or document bodies. Downloaded source text exists only in memory during the adapter call and is then passed to the existing local ingestion store, whose workspace must be treated as private. No Google API writes occur in this version.

The public repository contains only synthetic examples. Before publishing a clone, check `git status`, `git diff`, ignored files, and a repository-wide search for account addresses, real IDs, OAuth material, and absolute machine paths.
