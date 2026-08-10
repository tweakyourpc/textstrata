"""Acquisition-facing application helpers."""

from __future__ import annotations

from typing import Any


def acquisition_queue_payload(service: Any) -> dict[str, Any]:
    return service.list_jobs()


def acquisition_maintenance_settings_payload(service: Any) -> dict[str, Any]:
    return service.get_settings()


def save_acquisition_maintenance_settings(service: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return service.save_settings(payload)


def clear_acquisition_completed(service: Any) -> dict[str, int]:
    return {"cleared": service.clear_completed()}


def build_ingest_submission(service: Any, fields: dict[str, Any]) -> dict[str, Any]:
    keep = str(fields.get("keep_original", "")).lower() in {"1", "true", "yes", "on"}
    ocr_mode = str(fields.get("ocr_mode", "both")).strip().lower()
    if ocr_mode not in ("both", "image", "text"):
        ocr_mode = "both"
    common = {
        "title": str(fields.get("title") or ""),
        "notes": str(fields.get("notes") or ""),
        "keep_original": keep,
        "ocr_mode": ocr_mode,
    }
    if fields.get("file") is not None:
        job_id = service.enqueue_file(
            fields["file"],
            str(fields.get("filename") or "upload.bin"),
            media_type=str(fields.get("media_type") or ""),
            **common,
        )
    elif str(fields.get("url") or "").strip():
        if hasattr(service, "enqueue_url_result"):
            return service.enqueue_url_result(str(fields["url"]).strip(), **common)
        job_id = service.enqueue_url(str(fields["url"]).strip(), **common)
    else:
        raise ValueError("a URL or file is required")
    return {"job_id": job_id, "status": "queued"}
