# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Shared image byte compression for LLM context (tools, hooks, delegation)."""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover - optional import fallback
    Image = None
    ImageOps = None


def compress_image_available() -> bool:
    """True when Pillow is available for resize/re-encode."""
    return Image is not None and ImageOps is not None


def compress_image_bytes(
    raw: bytes,
    mime: str,
    *,
    image_max_edge: int = 768,
    image_jpeg_quality: int = 55,
    image_png_compress_level: int = 9,
) -> tuple[bytes, str]:
    """Downscale/compress image bytes. Returns original on failure or if output is not smaller."""
    if not raw or not compress_image_available():
        return raw, mime or "image/png"
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)
            width, height = img.size
            if width <= 0 or height <= 0:
                return raw, mime or "image/png"
            scale = min(1.0, float(image_max_edge) / float(max(width, height))) if image_max_edge > 0 else 1.0
            if scale < 1.0:
                new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            has_alpha = "A" in img.getbands() or "transparency" in getattr(img, "info", {})
            out = io.BytesIO()
            out_mime = mime or "image/png"
            if has_alpha:
                if img.mode not in {"RGBA", "LA"}:
                    img = img.convert("RGBA")
                out_mime = "image/png"
                img.save(
                    out,
                    format="PNG",
                    optimize=True,
                    compress_level=max(0, min(9, int(image_png_compress_level))),
                )
            else:
                if img.mode not in {"RGB", "L"}:
                    img = img.convert("RGB")
                out_mime = "image/jpeg"
                img.save(
                    out,
                    format="JPEG",
                    optimize=True,
                    quality=max(20, min(95, int(image_jpeg_quality))),
                )
            optimized = out.getvalue()
            if not optimized:
                return raw, mime or "image/png"
            ow, oh = img.size
            if len(optimized) < len(raw):
                logger.info(
                    "image_context_compress: reduced bytes %d -> %d pixels %dx%d -> %dx%d mime_in=%s mime_out=%s",
                    len(raw),
                    len(optimized),
                    width,
                    height,
                    ow,
                    oh,
                    mime or "",
                    out_mime,
                )
                return optimized, out_mime
            logger.debug(
                "image_context_compress: no_byte_reduction bytes=%d pixels=%dx%d mime_in=%s mime_out=%s",
                len(optimized),
                ow,
                oh,
                mime or "",
                out_mime,
            )
    except Exception:
        return raw, mime or "image/png"
    return raw, mime or "image/png"


# Backwards-compatible name for call sites that speak in "context" terms.
compress_image_for_context = compress_image_bytes
