# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Apply local image style transforms by configurable style profile."""

from __future__ import annotations

import json
import os
from typing import Any

from fairyclaw.sdk.group_runtime import expect_group_config
from fairyclaw.sdk.tools import ToolContext, resolve_safe_path

try:
    from fairyclaw_plugins.image_ops.config import ImageOpsRuntimeConfig
except Exception:  # pragma: no cover - direct test imports may bypass plugin loader
    from fairyclaw.capabilities.image_ops.config import ImageOpsRuntimeConfig

from ._shared import parse_input_ref, resolve_image_payload


def _ensure_pillow():
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
        import numpy as np
    except Exception as exc:
        raise RuntimeError("Pillow and numpy are required for apply_image_style.") from exc
    return Image, ImageEnhance, ImageFilter, ImageOps, np


def _quantize_rgb(np_image, bins: int):
    step = max(1, int(256 / max(2, bins)))
    return (np_image // step) * step


def _style_transform(img, profile: str, intensity: float, preserve_edges: bool, seed: int | None):
    Image, ImageEnhance, ImageFilter, ImageOps, np = _ensure_pillow()
    if seed is not None:
        np.random.seed(seed)

    arr = np.array(img.convert("RGB"))
    out = arr.astype(np.float32)

    if profile == "toon_v1":
        smoothed = np.array(img.filter(ImageFilter.MedianFilter(size=3)))
        quant = _quantize_rgb(smoothed, bins=16).astype(np.float32)
        if preserve_edges:
            edge = np.array(img.convert("L").filter(ImageFilter.FIND_EDGES)).astype(np.float32) / 255.0
            edge = 1.0 - np.clip(edge * 1.8, 0.0, 1.0)
            quant *= edge[..., None]
        out = 0.35 * out + 0.65 * quant

    elif profile == "watercolor_v1":
        blur = np.array(img.filter(ImageFilter.GaussianBlur(radius=2)))
        flat = _quantize_rgb(blur, bins=28).astype(np.float32)
        noise = np.random.normal(0.0, 8.0, size=flat.shape)
        out = np.clip(flat + noise * intensity, 0, 255)

    elif profile == "oilpaint_v1":
        med = np.array(img.filter(ImageFilter.MedianFilter(size=5))).astype(np.float32)
        sat = ImageEnhance.Color(Image.fromarray(med.astype("uint8"))).enhance(1.15 + 0.35 * intensity)
        out = np.array(sat, dtype=np.float32)

    elif profile == "pixelart_v1":
        w, h = img.size
        down = max(8, int(min(w, h) * 0.22))
        pix = img.resize((max(8, int(w * down / min(w, h))), down), resample=Image.Resampling.BOX)
        pix = pix.resize((w, h), resample=Image.Resampling.NEAREST)
        out = _quantize_rgb(np.array(pix), bins=24).astype(np.float32)

    elif profile == "sketch_v1":
        gray = ImageOps.grayscale(img)
        inv = ImageOps.invert(gray)
        blur = inv.filter(ImageFilter.GaussianBlur(radius=6.0))
        gray_np = np.array(gray).astype(np.float32)
        blur_np = np.array(blur).astype(np.float32)
        dodge = np.clip(gray_np * 255.0 / (255.0 - blur_np + 1e-5), 0, 255)
        out = np.stack([dodge, dodge, dodge], axis=-1)

    out = np.clip((1.0 - intensity) * arr + intensity * out, 0, 255).astype("uint8")
    return Image.fromarray(out, mode="RGB")


async def execute(args: dict[str, Any], context: ToolContext) -> str:
    """Apply one style profile and save styled image."""
    cfg = expect_group_config(context, ImageOpsRuntimeConfig)
    input_ref, err = parse_input_ref(args)
    if err:
        return err

    output_path = args.get("output_path")
    if not isinstance(output_path, str) or not output_path.strip():
        return "Error: output_path is required."
    safe_out, err = resolve_safe_path(output_path, context.filesystem_root_dir, context.workspace_root)
    if err or safe_out is None:
        return err or "Error: invalid output_path."

    style_profile = str(args.get("style_profile") or "").strip()
    if not style_profile:
        return "Error: style_profile is required."
    if style_profile not in cfg.style_profiles:
        return f"Error: unsupported style_profile '{style_profile}'."

    intensity = args.get("intensity")
    if not isinstance(intensity, (int, float)):
        intensity = 0.7
    intensity = max(0.0, min(1.0, float(intensity)))
    preserve_edges = bool(args.get("preserve_edges", True))
    seed = args.get("seed") if isinstance(args.get("seed"), int) else None

    payload, err = await resolve_image_payload(
        context,
        input_ref=input_ref or {},
        max_bytes=cfg.max_image_bytes,
    )
    if err:
        return err
    assert payload is not None

    try:
        Image, _, _, _, _ = _ensure_pillow()
        from io import BytesIO

        src = Image.open(BytesIO(payload.raw_bytes)).convert("RGB")
        result = _style_transform(src, style_profile, intensity, preserve_edges, seed)
        os.makedirs(os.path.dirname(safe_out.path), exist_ok=True)
        if max(result.size) < cfg.min_output_edge:
            return "Error: transformed image too small after style operation."
        result.save(safe_out.path, format="PNG", compress_level=cfg.png_compress_level)
        size = os.path.getsize(safe_out.path)
        return json.dumps(
            {
                "ok": True,
                "output_path": safe_out.path,
                "profile_id": style_profile,
                "metrics": {
                    "width": result.width,
                    "height": result.height,
                    "bytes": size,
                    "intensity": intensity,
                    "preserve_edges": preserve_edges,
                },
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return f"Error: apply_image_style failed: {exc}"
