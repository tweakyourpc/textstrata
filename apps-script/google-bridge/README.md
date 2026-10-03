# TextStrata Google Apps Script bridge

This is the deployable Apps Script project. The web app executes as its deploying user. It accepts only the signed Inbox, Library mirror, and explicitly flagged Library revision actions listed by authenticated `ping`. Google resource IDs are Script Properties or IDs verified against the configured Inbox and Library. The local TextStrata process needs no Google OAuth for this transport. Library edits require `Status=Updated` in the Library index; ordinary mirroring refuses pending/conflicted rows. See [the setup guide](../../docs/google-bridge-setup.md).

Use [the production setup guide](../../docs/google-bridge-setup.md) for Script Properties, exact clasp commands, and the private TextStrata configuration. `.clasp.json` is ignored because it contains your own script ID. Copy `.clasp.example.json` to `.clasp.json` and replace the placeholder after creating your Apps Script project.

Before push, run from the TextStrata repository root:

```bash
node --check apps-script/google-bridge/Code.js
node tests/test_google_bridge.js
```

The web app must be deployed as `Execute as: Me` and `Who has access: Anyone`. Every privileged POST checks the signature, timestamp, nonce, and action. A public URL alone grants no capability.
