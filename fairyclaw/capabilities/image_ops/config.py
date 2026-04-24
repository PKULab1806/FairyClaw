# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Runtime configuration for ImageOps capability group."""

from pydantic import BaseModel, Field


class ImageOpsRuntimeConfig(BaseModel):
    """Frozen config snapshot for image context injection and local editing."""

    model_config = {"frozen": True}

    max_image_bytes: int = 8 * 1024 * 1024
    min_output_edge: int = 96
    jpeg_quality: int = 95
    png_compress_level: int = 2
    style_profiles: dict[str, dict[str, float | int | str]] = Field(
        default_factory=lambda: {
            "toon_v1": {"bilateral_d": 7, "bilateral_sigma": 60.0, "quant_bins": 16},
            "watercolor_v1": {"smooth_radius": 2, "blend_alpha": 0.45, "noise_strength": 0.06},
            "oilpaint_v1": {"kernel": 5, "contrast_gain": 1.12},
            "pixelart_v1": {"downsample": 0.22, "palette_bins": 24},
            "sketch_v1": {"blur_sigma": 6.0, "line_gain": 1.0},
        }
    )
    background_presets: dict[str, dict[str, object]] = Field(
        default_factory=lambda: {
            "moon": {"top": "#12152A", "bottom": "#3A3E68", "stars": 80},
            "beach": {"top": "#6DC9FF", "bottom": "#F5D6A1", "waves": 4},
            "space": {"top": "#06070F", "bottom": "#1D2754", "stars": 140},
            "study": {"top": "#7A5A42", "bottom": "#D6C2A5", "grid": True},
        }
    )


runtime_config_model = ImageOpsRuntimeConfig
