# Optional control plane

The control plane is an optional operational companion for a TextStrata workspace. It supports one-way Markdown backup and explicitly approved remote ingest through `rclone`. It does not require Google credentials in TextStrata, does not run as a resident service, and does not perform bidirectional synchronization.

Copy `config/control.example.json` to `.fabric/control.json` and edit the target paths. `TEXTSTRATA_CONTROL_CONFIG` can point to a machine-local configuration instead. Keep rclone credentials outside the repository.

```bash
textstrata control doctor
textstrata control backup --dry-run
textstrata control backup
textstrata control ingest
textstrata control run
```

Backups select configured Markdown roots, exclude `private` and `draft` tags by default, and write a SHA-256 manifest under `.fabric/control-state/` as well as uploading `backup-manifest.json` beside the remote mirror. Remote ingest reads a JSON queue, processes only `status: approved` entries, records processed IDs locally, and waits for queued acquisition jobs.

Spreadsheet workflows may provide approval records, but execution stays on the local host. Public web submissions must never imply automatic approval.
