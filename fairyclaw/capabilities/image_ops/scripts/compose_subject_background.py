# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Compose subject with a replaced/generated background."""

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


def _ensure_imaging_stack():
    try:
        from PIL import Image, ImageDraw, ImageFilter
        import numpy as np
    except Exception as exc:
        raise RuntimeError("Pillow and numpy are required for compose_subject_background.") from exc
    return Image, ImageDraw, ImageFilter, np


def _segment_subject(image, mode: str):
    Image, _, ImageFilter, np = _ensure_imaging_stack()
    gray = image.convert("L")
    arr = np.array(gray)
    h, w = arr.shape
    threshold = float(arr.mean())
    mask = (arr < threshold).astype("uint8") * 255

    if mode in {"grabcut", "auto"}:
        try:
            import cv2  # type: ignore[import-untyped]

            bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
            gc_mask = np.zeros((h, w), np.uint8)
            rect = (max(1, w // 12), max(1, h // 12), max(2, w - w // 6), max(2, h - h // 6))
            bgd_model = np.zeros((1, 65), np.float64)
            fgd_model = np.zeros((1, 65), np.float64)
            cv2.grabCut(bgr, gc_mask, rect, bgd_model, fgd_model, 4, cv2.GC_INIT_WITH_RECT)
            gc_bin = ((gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD)).astype("uint8") * 255
            if gc_bin.mean() > 3:
                mask = gc_bin
        except Exception:
            pass

    mask_img = Image.fromarray(mask, mode="L").filter(ImageFilter.GaussianBlur(radius=3))
    quality = float(np.count_nonzero(mask) / mask.size)
    fallback_used = quality < 0.05 or quality > 0.95
    if fallback_used:
        fallback = np.zeros_like(mask)
        y1, y2 = h // 8, h - h // 8
        x1, x2 = w // 8, w - w // 8
        fallback[y1:y2, x1:x2] = 255
        mask_img = Image.fromarray(fallback, mode="L").filter(ImageFilter.GaussianBlur(radius=5))
        quality = float((fallback > 0).sum() / fallback.size)
    return mask_img, quality, fallback_used


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.strip().lstrip("#")
    if len(value) != 6:
        return (32, 32, 32)
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _generate_background(size: tuple[int, int], spec: dict[str, Any], cfg: ImageOpsRuntimeConfig):
    Image, ImageDraw, _, np = _ensure_imaging_stack()
    w, h = size
    bg_type = str(spec.get("type") or "preset")
    params = spec.get("id_or_params")
    params = params if isinstance(params, dict) else {}

    if bg_type == "preset":
        preset_id = str(params.get("id") or "space")
        preset = cfg.background_presets.get(preset_id, cfg.background_presets.get("space", {}))
        top = _hex_to_rgb(str(preset.get("top", "#0A0A14")))
        bottom = _hex_to_rgb(str(preset.get("bottom", "#2B2D45")))
        stars = int(preset.get("stars", 0))
    else:
        top = _hex_to_rgb(str(params.get("top", "#1B2A44")))
        bottom = _hex_to_rgb(str(params.get("bottom", "#A9C7E8")))
        stars = int(params.get("stars", 0))

    grad = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        t = y / max(1, h - 1)
        grad[y, :, 0] = int((1 - t) * top[0] + t * bottom[0])
        grad[y, :, 1] = int((1 - t) * top[1] + t * bottom[1])
        grad[y, :, 2] = int((1 - t) * top[2] + t * bottom[2])
    bg = Image.fromarray(grad, mode="RGB")
    draw = ImageDraw.Draw(bg)
    if stars > 0:
        rng = np.random.default_rng(42)
        for _ in range(stars):
            x = int(rng.integers(0, max(1, w)))
            y = int(rng.integers(0, max(1, h // 2)))
            r = int(rng.integers(1, 3))
            draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255))
    return bg


def _anchor_position(anchor: str, canvas_w: int, obj_w: int, margin: int) -> int:
    if anchor == "left":
        return margin
    if anchor == "right":
        return max(margin, canvas_w - obj_w - margin)
    return max(margin, (canvas_w - obj_w) // 2)


async def execute(args: dict[str, Any], context: ToolContext) -> str:
    """Compose segmented subject over generated/preset background."""
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

    bg_spec = args.get("background_spec")
    if not isinstance(bg_spec, dict):
        return "Error: background_spec must be an object."
    subject_policy = args.get("subject_policy") if isinstance(args.get("subject_policy"), dict) else {}
    placement = args.get("placement") if isinstance(args.get("placement"), dict) else {}
    blend_policy = args.get("blend_policy") if isinstance(args.get("blend_policy"), dict) else {}

    payload, err = await resolve_image_payload(
        context,
        input_ref=input_ref or {},
        max_bytes=cfg.max_image_bytes,
    )
    if err:
        return err
    assert payload is not None

    try:
        Image, _, ImageFilter, np = _ensure_imaging_stack()
        from io import BytesIO

        src = Image.open(BytesIO(payload.raw_bytes)).convert("RGB")
        mask_mode = str(subject_policy.get("segmentation_mode") or "auto")
        subject_mask, mask_quality, fallback_used = _segment_subject(src, mask_mode)

        bg = _generate_background(src.size, bg_spec, cfg).convert("RGB")
        subject_rgba = src.convert("RGBA")
        subject_rgba.putalpha(subject_mask)

        scale = float(placement.get("scale", 1.0))
        scale = min(1.8, max(0.2, scale))
        margin_ratio = float(placement.get("margin_ratio", 0.05))
        margin_ratio = min(0.4, max(0.0, margin_ratio))

        w, h = src.size
        new_w = max(cfg.min_output_edge, int(w * scale))
        new_h = max(cfg.min_output_edge, int(h * scale))
        subject_rgba = subject_rgba.resize((new_w, new_h), Image.Resampling.LANCZOS)
        composed = bg.copy()

        anchor = str(placement.get("anchor") or "center")
        margin = int(min(w, h) * margin_ratio)
        x = _anchor_position(anchor, w, new_w, margin)
        y = max(margin, h - new_h - margin)

        feather_radius = int(blend_policy.get("feather_radius", 8))
        shadow_strength = float(blend_policy.get("shadow_strength", 0.3))
        color_match_strength = float(blend_policy.get("color_match_strength", 0.4))
        feather_radius = max(0, min(30, feather_radius))
        shadow_strength = max(0.0, min(1.0, shadow_strength))
        color_match_strength = max(0.0, min(1.0, color_match_strength))

        if feather_radius > 0:
            alpha = subject_rgba.split()[-1].filter(ImageFilter.GaussianBlur(radius=feather_radius))
            subject_rgba.putalpha(alpha)

        shadow = Image.new("RGBA", subject_rgba.size, (0, 0, 0, int(120 * shadow_strength)))
        shadow_alpha = subject_rgba.split()[-1].filter(ImageFilter.GaussianBlur(radius=12))
        shadow.putalpha(shadow_alpha)
        composed_rgba = composed.convert("RGBA")
        composed_rgba.alpha_composite(shadow, (x + 6, y + 8))
        composed_rgba.alpha_composite(subject_rgba, (x, y))

        out_rgb = composed_rgba.convert("RGB")
        if color_match_strength > 0:
            out_np = np.array(out_rgb).astype(np.float32)
            bg_np = np.array(bg).astype(np.float32)
            out_np = (1.0 - 0.2 * color_match_strength) * out_np + (0.2 * color_match_strength) * bg_np
            out_rgb = Image.fromarray(np.clip(out_np, 0, 255).astype("uint8"), mode="RGB")

        os.makedirs(os.path.dirname(safe_out.path), exist_ok=True)
        out_rgb.save(safe_out.path, format="PNG", compress_level=cfg.png_compress_level)
        size = os.path.getsize(safe_out.path)
        return json.dumps(
            {
                "ok": True,
                "output_path": safe_out.path,
                "foreground_mask_quality": round(mask_quality, 4),
                "fallback_used": bool(fallback_used),
                "metrics": {
                    "width": out_rgb.width,
                    "height": out_rgb.height,
                    "bytes": size,
                    "segmentation_mode": mask_mode,
                    "anchor": anchor,
                    "scale": scale,
                },
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return f"Error: compose_subject_background failed: {exc}"
