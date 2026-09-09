"""Orthographic vertical fit, with image coordinates measured from the top."""


def fullbody_fit(z_min, z_max, x_span, aspect, height_fraction, foot_y,
                 head_margin=0.06, foot_margin=0.06):
    if not z_max > z_min or aspect <= 0:
        raise ValueError("Invalid subject bounds or aspect ratio")
    fraction = min(height_fraction, 1 - head_margin - foot_margin)
    if fraction <= 0:
        raise ValueError("Invalid height fraction")
    span = max((z_max - z_min) / fraction, x_span / (aspect * 0.88))
    actual_fraction = (z_max - z_min) / span
    resolved_foot = max(head_margin + actual_fraction,
                        min(1 - foot_margin, foot_y))
    # y_image = 0.5 + (camera_z - world_z) / visible_vertical_span.
    center_z = z_min + (resolved_foot - 0.5) * span
    return {
        "ortho_scale": span * aspect, "center_z": center_z,
        "top_y": resolved_foot - actual_fraction, "foot_y": resolved_foot,
        "height_fraction": actual_fraction,
    }
