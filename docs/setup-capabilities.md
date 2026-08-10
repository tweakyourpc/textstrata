# Setup and Capability Center

TextStrata has a deterministic local core: Markdown/filesystem storage and a rebuildable SQLite catalog. `/setup` and `GET /api/textstrata/setup/status` report workspace readiness and optional tools without installing packages, starting services, contacting Ollama, downloading models, or creating notes.

`POST /api/textstrata/setup/initialize` is idempotent. It creates only the standard workspace directories. `textstrata init` is the equivalent CLI mutation; `textstrata doctor` and `textstrata doctor --json` are read-only diagnostics.

Optional capability cards are informational. Documents, images/OCR, YouTube, local audio transcription, and AI-assisted commands can be installed or configured independently. Missing optional tools do not make `/healthz` unhealthy. Model downloads are always explicit and are marked in the status payload where a tool may download on first use.

The setup use case in `src/textstrata/application/setup.py` owns these checks. New UI or CLI capability detection should call it instead of implementing a second environment probe.
