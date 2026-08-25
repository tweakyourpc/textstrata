# 0.6.0 Control Plane Increment

The first 0.6.0 vertical slice makes backup and restore an explicit product
workflow rather than a hidden operator script.

- Backup preview is read-only and uses the same tag filters as the configured
  backup operation.
- Backup execution remains an explicit confirmed mutation.
- Restore preview verifies a local `backup-manifest.json` without copying.
- Restore requires a confirmed new or empty destination and performs a second
  verification after copying.
- CLI, web, and MCP expose the same safety boundary.
- The active workspace is never an allowed web restore destination.

The Google Drive transport remains optional and host-managed through rclone.
The next increment should add remote restore staging and a durable backup
history before calling the full control plane complete.
