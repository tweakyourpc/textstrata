# Deterministic acquisition

Acquisition is idempotent for queued, processing, and published sources. A
repeat submission returns the existing job and, when available, its item. It
does not refresh or replace an existing note; refresh remains an explicit
future operation.

YouTube watch, short, Shorts, embed, and live URLs normalize to
`youtube:video:<id>`. Playlists, channels, and handles normalize to distinct
`youtube:collection:<canonical-url>` identities. Tracking parameters are
discarded. Inspect a value without opening SQLite with:

`GET /api/acquisition/source-identity?url=<url>`

Caption selection is stable: manually authored English captions win over
automatic English captions, followed by other English variants and then other
languages. Cue normalization removes exact adjacent duplicates, collapses
whitespace, clamps overlaps, preserves valid end times, and infers missing
durations consistently. A job records whether captions were available,
unavailable, or failed to download, plus language, origin, and tool metadata.

Historical jobs are not merged or deleted. Ambiguous historical duplicates are
left without a backfilled identity; only newly identified queued, processing,
and completed jobs participate in the unique identity index. Failed and
cancelled jobs can be submitted again explicitly.
