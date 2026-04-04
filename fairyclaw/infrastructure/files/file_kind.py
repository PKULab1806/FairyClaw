# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Describe uploaded file bytes for LLM-facing user hints (magic bytes first)."""

from __future__ import annotations

import mimetypes
from typing import Final

import filetype  # type: ignore[import-untyped]

_GENERIC_NAMES: Final[frozenset[str]] = frozenset({"upload", "unnamed", ""})


def _label_from_mime(mime: str | None) -> str:
    if not mime:
        return "未知类型"
    m = mime.split(";")[0].strip().lower()
    table: dict[str, str] = {
        "image/png": "PNG 图片",
        "image/jpeg": "JPEG 图片",
        "image/jpg": "JPEG 图片",
        "image/gif": "GIF 图片",
        "image/webp": "WebP 图片",
        "image/svg+xml": "SVG 矢量图",
        "image/bmp": "BMP 图片",
        "image/tiff": "TIFF 图片",
        "application/pdf": "PDF 文档",
        "text/plain": "纯文本",
        "text/markdown": "Markdown 文本",
        "text/html": "HTML 文档",
        "application/json": "JSON 文本",
        "application/zip": "ZIP 压缩包",
        "application/x-tar": "TAR 归档",
        "application/gzip": "GZIP 压缩包",
        "audio/mpeg": "MP3 音频",
        "audio/wav": "WAV 音频",
        "video/mp4": "MP4 视频",
    }
    if m in table:
        return table[m]
    if m.startswith("image/"):
        return "图片"
    if m.startswith("video/"):
        return "视频"
    if m.startswith("audio/"):
        return "音频"
    if m.startswith("text/"):
        return "文本"
    return "文件"


def describe_user_upload_for_llm(
    content: bytes,
    *,
    mime_type: str | None = None,
    filename: str | None = None,
) -> str:
    """One short Chinese line: what the user uploaded, preferring magic-byte sniffing."""
    kind = filetype.guess(content) if content else None
    effective_mime: str | None = kind.mime if kind else None
    if effective_mime is None and mime_type:
        effective_mime = mime_type.split(";")[0].strip().lower() or None
    if effective_mime is None and filename:
        guessed, _ = mimetypes.guess_type(filename)
        effective_mime = guessed
    label = _label_from_mime(effective_mime)
    base = f"用户上传了一个 {label} 类型的文件。"
    fn = (filename or "").strip()
    if fn and fn not in _GENERIC_NAMES:
        return f"{base} 原始文件名：{fn}。"
    return base
