import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from textstrata.acquisition import (
    AcquisitionService,
    _whisper_threads,
    _whisper_tool,
    _whisper_tuning_flags,
    _worker_enabled,
    capabilities,
)
from textstrata.captions import (
    CaptionCue,
    export_caption,
    has_timestamped_transcript,
    parse_markdown_transcript,
    parse_webvtt,
    render_srt,
    render_webvtt,
    normalize_cues,
)
from textstrata.store import TextStrataStore


class CaptionFormatTests(unittest.TestCase):
    def test_normalize_cues_collapses_rolling_caption_windows_but_keeps_later_repetition(self):
        cues = normalize_cues([
            CaptionCue(1_000, "Hello", 1_500),
            CaptionCue(1_000, "Hello world", 1_700),
            CaptionCue(2_000, "Hello world", 2_500),
        ])
        self.assertEqual(cues, [
            CaptionCue(1_000, "Hello world", 1_700),
            CaptionCue(2_000, "Hello world", 2_500),
        ])

    def test_normalize_cues_does_not_drop_unrelated_same_timestamp_speech(self):
        normalized = normalize_cues([CaptionCue(1_000, "Speaker one"), CaptionCue(1_000, "Speaker two")])
        self.assertEqual([cue.text for cue in normalized], ["Speaker one", "Speaker two"])
        self.assertEqual([cue.start_ms for cue in normalized], [1_000, 1_000])

    def test_normalize_cues_collapses_provider_rollover_with_millisecond_drift(self):
        normalized = normalize_cues([
            CaptionCue(16_670, "This video is a very deep dive on all of", 16_680),
            CaptionCue(16_680, "This video is a very deep dive on all of the different ways that you can covertly", 18_750),
        ])
        self.assertEqual(len(normalized), 1)
        self.assertIn("different ways", normalized[0].text)

    def test_webvtt_and_srt_syntax_and_default_end_times(self):
        cues = [
            CaptionCue(1_250, "First & <second>"),
            CaptionCue(4_000, "Middle"),
            CaptionCue(7_500, "Last"),
        ]

        vtt = render_webvtt(cues)
        self.assertEqual(
            vtt,
            "WEBVTT\n\n"
            "00:00:01.250 --> 00:00:04.000\n"
            "First &amp; &lt;second&gt;\n\n"
            "00:00:04.000 --> 00:00:07.500\n"
            "Middle\n\n"
            "00:00:07.500 --> 00:00:10.500\n"
            "Last\n",
        )
        self.assertRegex(vtt, r"^WEBVTT\n\n")
        self.assertEqual(
            re.findall(r"(?m)^\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}$", vtt),
            [
                "00:00:01.250 --> 00:00:04.000",
                "00:00:04.000 --> 00:00:07.500",
                "00:00:07.500 --> 00:00:10.500",
            ],
        )

        srt = render_srt(cues)
        self.assertEqual(
            srt,
            "1\n00:00:01,250 --> 00:00:04,000\nFirst & <second>\n\n"
            "2\n00:00:04,000 --> 00:00:07,500\nMiddle\n\n"
            "3\n00:00:07,500 --> 00:00:10,500\nLast\n",
        )

    def test_webvtt_parser_preserves_provided_end_times(self):
        source = """WEBVTT

00:00:01.125 --> 00:00:02.875
Hello <c.highlight>world</c> &amp; everyone

cue-2
00:00:03.000 --> 00:00:06.500 align:start
Second cue
"""
        cues = parse_webvtt(source)
        self.assertEqual(
            cues,
            [
                CaptionCue(1_125, "Hello world & everyone", 2_875),
                CaptionCue(3_000, "Second cue", 6_500),
            ],
        )
        self.assertIn("00:00:01.125 --> 00:00:02.875", render_webvtt(cues))
        self.assertIn("00:00:03,000 --> 00:00:06,500", render_srt(cues))

    def test_markdown_parser_accepts_optional_explicit_end(self):
        body = """# Video

## Timestamped transcript

[00:01.250 --> 00:02.500] Exact duration.
[00:04] Inferred duration.

## Notes

[00:10] Not part of the transcript.
"""
        self.assertEqual(
            parse_markdown_transcript(body),
            [
                CaptionCue(1_250, "Exact duration.", 2_500),
                CaptionCue(4_000, "Inferred duration.", None),
            ],
        )


class CaptionAcquisitionTests(unittest.TestCase):
    def test_youtube_ingestion_persists_caption_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TextStrataStore(tmp)
            store.ensure_dirs()
            service = AcquisitionService(store)
            cues = [
                CaptionCue(1_000, "One", 2_250),
                CaptionCue(3_000, "Two", 4_750),
            ]
            markdown = """# Caption Fixture

Source: https://www.youtube.com/watch?v=fixture

## Timestamped transcript

[00:01] One
[00:03] Two
"""
            try:
                with patch.object(
                    service,
                    "_convert_youtube",
                    return_value=(
                        markdown,
                        "https://www.youtube.com/watch?v=fixture",
                        {"_caption_cues": cues},
                    ),
                ):
                    item_id = service._process(
                        {
                            "type": "youtube",
                            "payload": "https://www.youtube.com/watch?v=fixture",
                            "title": "",
                            "original_name": "",
                            "notes": "caption export fixture",
                        }
                    )
            finally:
                service.close()

            self.assertEqual(item_id, "caption-fixture")
            artifact_dir = Path(tmp) / ".fabric" / "caption-exports"
            self.assertEqual(
                (artifact_dir / "caption-fixture.vtt").read_text(encoding="utf-8"),
                "WEBVTT\n\n"
                "00:00:01.000 --> 00:00:02.250\nOne\n\n"
                "00:00:03.000 --> 00:00:04.750\nTwo\n",
            )
            self.assertEqual(
                (artifact_dir / "caption-fixture.srt").read_text(encoding="utf-8"),
                "1\n00:00:01,000 --> 00:00:02,250\nOne\n\n"
                "2\n00:00:03,000 --> 00:00:04,750\nTwo\n",
            )
            manifest = json.loads((artifact_dir / "caption-fixture.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["cue_count"], 2)

            item = service.store.normalized_path_for_id(item_id)
            self.assertIsNotNone(item)
            from textstrata.ingest import build_item

            parsed_item = build_item(item.read_text(encoding="utf-8"), fallback_id=item_id)[0]
            self.assertIn("00:00:02.250", export_caption(Path(tmp), parsed_item, "vtt"))


class AudioTranscriptionTests(unittest.TestCase):
    def test_whisper_tool_prefers_env_override_then_known_cli_names(self):
        with patch("textstrata.acquisition._tool", side_effect=lambda name: f"/usr/bin/{name}"):
            with patch.dict("os.environ", {"FABRIC_WHISPER_BIN": "custom-whisper"}, clear=False):
                self.assertEqual(_whisper_tool(), "/usr/bin/custom-whisper")
            with patch.dict("os.environ", {}, clear=False) as env:
                env.pop("FABRIC_WHISPER_BIN", None)
                self.assertEqual(_whisper_tool(), "/usr/bin/whisper-ctranslate2")

        def only_openai_whisper(name):
            return "/usr/bin/whisper" if name == "whisper" else None

        with patch("textstrata.acquisition._tool", side_effect=only_openai_whisper):
            with patch.dict("os.environ", {}, clear=False) as env:
                env.pop("FABRIC_WHISPER_BIN", None)
                self.assertEqual(_whisper_tool(), "/usr/bin/whisper")

    def test_capabilities_reports_audio_only_with_ffmpeg_and_whisper(self):
        with patch("textstrata.acquisition._tool", side_effect=lambda name: None if name == "ffmpeg" else f"/usr/bin/{name}"):
            self.assertFalse(capabilities()["audio_transcription"])
        with patch("textstrata.acquisition._tool", side_effect=lambda name: f"/usr/bin/{name}"):
            with patch.dict("os.environ", {}, clear=False) as env:
                env.pop("FABRIC_WHISPER_BIN", None)
                caps = capabilities()
        self.assertTrue(caps["audio_transcription"])
        self.assertTrue(caps["tools"]["whisper"])

    def test_m4a_upload_is_transcribed_into_a_timestamped_transcript(self):
        cues = [CaptionCue(0, "Hello there", 1_500), CaptionCue(2_000, "Second line", 3_500)]
        with tempfile.TemporaryDirectory() as tmp:
            store = TextStrataStore(tmp)
            store.ensure_dirs()
            service = AcquisitionService(store)
            try:
                with patch.object(AcquisitionService, "_transcribe", return_value=cues):
                    markdown, source, assets, meta = service._convert_audio(b"fake-m4a-bytes", "voice-note.m4a", "audio/mp4")
                    self.assertEqual(source, "")
                    self.assertEqual(len(assets), 1)
                    self.assertEqual(meta["_caption_cues"], cues)
                    self.assertIn("audio", meta["_extra_tags"])
                    self.assertIn("# voice note", markdown)
                    self.assertIn("## Timestamped transcript", markdown)
                    self.assertIn("[00:00] Hello there", markdown)
                    self.assertIn("[00:02] Second line", markdown)

                    item_id = service._process(
                        {
                            "type": "file",
                            "payload": json.dumps({"path": _write_temp(tmp, b"fake-m4a-bytes"), "media_type": "audio/mp4"}),
                            "title": "",
                            "original_name": "voice-note.m4a",
                            "notes": "",
                            "ocr_mode": "both",
                        }
                    )
            finally:
                service.close()

            self.assertEqual(item_id, "voice-note")
            artifact_dir = Path(tmp) / ".fabric" / "caption-exports"
            self.assertIn("00:00:01.500", (artifact_dir / "voice-note.vtt").read_text(encoding="utf-8"))

            from textstrata.ingest import build_item

            published = store.normalized_path_for_id(item_id)
            self.assertIsNotNone(published)
            parsed = build_item(published.read_text(encoding="utf-8"), fallback_id=item_id)[0]
            self.assertTrue(has_timestamped_transcript(parsed))
            self.assertIn("00:00:01.500", export_caption(Path(tmp), parsed, "vtt"))

    def test_video_uploads_route_to_local_transcription_not_markitdown(self):
        cues = [CaptionCue(500, "Recorded screen share", 2_000)]
        with tempfile.TemporaryDirectory() as tmp:
            store = TextStrataStore(tmp)
            store.ensure_dirs()
            service = AcquisitionService(store)
            try:
                with patch.object(AcquisitionService, "_transcribe", return_value=cues) as transcribe:
                    with patch.object(AcquisitionService, "_markitdown", side_effect=AssertionError("must not reach MarkItDown")):
                        markdown, _, assets, meta = service._convert_file(b"fake-mp4", "demo.mp4", "video/mp4")
                self.assertEqual(transcribe.call_count, 1)
                self.assertEqual(len(assets), 1)
                self.assertIn("transcript", meta["_extra_tags"])
                self.assertIn("[00:00] Recorded screen share", markdown)
            finally:
                service.close()

    def test_transcription_without_a_whisper_cli_fails_with_install_guidance(self):
        with patch("textstrata.acquisition._whisper_tool", return_value=None):
            with self.assertRaises(RuntimeError) as caught:
                AcquisitionService._transcribe(Path("/nonexistent.m4a"))
        self.assertIn("FABRIC_WHISPER_BIN", str(caught.exception))


def _write_temp(directory: str, data: bytes) -> str:
    path = Path(directory) / "upload.m4a"
    path.write_bytes(data)
    return str(path)


class AcquisitionWorkerGateTests(unittest.TestCase):
    """Several services share one .workspace, so only one may claim jobs from jobs.db."""

    def _service(self, tmp):
        store = TextStrataStore(tmp)
        store.ensure_dirs()
        return AcquisitionService(store)

    def test_worker_runs_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {}, clear=False) as env:
                env.pop("FABRIC_ACQUISITION_WORKER", None)
                service = self._service(tmp)
            try:
                self.assertIsNotNone(service._worker)
            finally:
                service.close()

    def test_disabled_worker_accepts_jobs_but_never_claims_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"FABRIC_ACQUISITION_WORKER": "0"}, clear=False):
                service = self._service(tmp)
                try:
                    self.assertIsNone(service._worker)
                    job_id = service.enqueue_file(b"hello", "note.txt", media_type="text/plain")
                    self.assertGreater(job_id, 0)
                    time.sleep(1.0)
                    job = [j for j in service.list_jobs()["jobs"] if j["id"] == job_id][0]
                    self.assertEqual(job["status"], "queued")
                    self.assertEqual(job["stage"], "queued")
                    self.assertIsNotNone(job["stage_updated_at"])
                    self.assertIsNone(job["started_at"])
                    self.assertIsNone(job["duration_seconds"])
                finally:
                    service.close()  # must not raise with no worker thread

    def test_gate_accepts_the_documented_falsey_spellings(self):
        for value, expected in (("0", False), ("false", False), ("no", False), ("off", False),
                                ("1", True), ("true", True), ("", True)):
            with patch.dict("os.environ", {"FABRIC_ACQUISITION_WORKER": value}, clear=False):
                self.assertEqual(_worker_enabled(), expected, f"value {value!r}")


class WhisperTuningTests(unittest.TestCase):
    def test_ctranslate2_gets_compute_type_and_openai_whisper_does_not(self):
        with patch.dict("os.environ", {}, clear=False) as env:
            env.pop("FABRIC_WHISPER_THREADS", None)
            env.pop("FABRIC_WHISPER_COMPUTE", None)
            ct2 = _whisper_tuning_flags("/usr/bin/whisper-ctranslate2")
            openai = _whisper_tuning_flags("/usr/bin/whisper")
        self.assertEqual(ct2, ["--threads", "1", "--compute_type", "int8"])
        self.assertEqual(openai, ["--threads", "1"])

    def test_env_overrides_threads_and_compute_type(self):
        with patch.dict("os.environ", {"FABRIC_WHISPER_THREADS": "4", "FABRIC_WHISPER_COMPUTE": "float32"}, clear=False):
            self.assertEqual(
                _whisper_tuning_flags("/opt/bin/whisper-ctranslate2"),
                ["--threads", "4", "--compute_type", "float32"],
            )

    def test_bad_thread_values_fall_back_to_the_safe_default(self):
        for value in ("0", "-2", "many", ""):
            with patch.dict("os.environ", {"FABRIC_WHISPER_THREADS": value}, clear=False):
                self.assertEqual(_whisper_threads(), 1, f"value {value!r}")
