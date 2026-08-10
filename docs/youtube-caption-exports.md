# YouTube Caption Exports

TextStrata generates WebVTT and SubRip artifacts when a YouTube video
ingestion returns caption cues. The Markdown note remains the readable source
in the knowledge library; exact source cue durations are retained separately
for subtitle downloads.

## Routes

- `GET /api/notes/{note_id}/export/vtt`
- `GET /api/notes/{note_id}/export/srt`

WebVTT responses use `text/vtt; charset=utf-8`. SubRip responses use
`application/x-subrip; charset=utf-8`. Both responses use an attachment
`Content-Disposition` with the note ID as the filename.

## Timing Contract

- VTT starts with `WEBVTT` and a blank line.
- VTT timestamps use `HH:MM:SS.mmm`.
- SRT timestamps use `HH:MM:SS,mmm`.
- SRT cue indexes begin at 1 and remain sequential.
- A missing cue end uses the next cue's start.
- A final cue without an end receives a three-second duration.
- An invalid end at or before its start receives the same deterministic
  fallback so every emitted cue has positive duration.

The parser preserves valid source WebVTT end times. New YouTube ingestions
store derived files and a source-body hash under
`.fabric/caption-exports/`. If the note body changes, the resolver derives a
fresh export from the current timestamped transcript instead of serving a
stale artifact.

## UI

YouTube notes with a `Timestamped transcript` section show a native HTML
`details` menu labeled **Export captions**. It links directly to both routes
and adds no caption-specific JavaScript. Notes without a qualifying YouTube
source and transcript do not show the menu.

## Verification

Run the focused contract tests:

```sh
PYTHONPATH=src python -m unittest \
  tests.test_captions tests.test_web tests.test_presentation
```

The format rules follow the
[W3C WebVTT specification](https://www.w3.org/TR/webvtt1/).
