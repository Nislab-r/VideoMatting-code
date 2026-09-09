#!/usr/bin/env python3
"""VLM gate for low-resolution temporal foreground/background composites."""

import argparse
import json
import os
import re
import subprocess
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw


PROMPT = """
You are quality-checking a five-frame temporal contact sheet of a synthetic human
composited into a real video. Return exactly one JSON object and no markdown:
{
  "person_on_valid_surface": boolean,
  "perspective_consistent": boolean,
  "scale_consistent": boolean,
  "placement_stable": boolean,
  "lighting_plausible": boolean,
  "physically_plausible": boolean,
  "score": number between 0.0 and 1.0,
  "fatal_reasons": [string],
  "notes": [string]
}
The requested framing is: {framing}.
For fullbody, require visible feet on a defensible near/mid-depth support surface.
For halfbody, feet may be outside the crop: judge a natural waist/mid-thigh crop,
credible portrait viewpoint, scale, uncluttered torso region, and coherent depth;
do not reject merely because the ground is outside the frame. For closeup_hair,
require a natural head-and-shoulders crop with the important hair silhouette inside
the frame; ground support is irrelevant. For every framing reject giant scale,
open-sky/facade placement that reads as floating, incompatible eye line/camera
height, obstacle overlap, and unexplained temporal sliding. Judge physical scene
compatibility, not photorealism or the stylized appearance of the 3D character.
""".strip()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--foreground-dir", type=Path, required=True)
    parser.add_argument("--background", type=Path, required=True)
    parser.add_argument("--background-start-sec", type=float, default=0.0)
    parser.add_argument("--frame-count", type=int, required=True)
    parser.add_argument("--framing", choices=("fullbody", "halfbody", "closeup_hair"), required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--gpu", default="0")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def background_frame(path, moment):
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", f"{moment:.6f}", "-i", str(path),
        "-frames:v", "1", "-vf", "scale=640:360:force_original_aspect_ratio=increase,crop=640:360",
        "-f", "image2pipe", "-vcodec", "png", "pipe:1",
    ], check=True, capture_output=True)
    return Image.open(BytesIO(result.stdout)).convert("RGBA")


def background_duration(path):
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ], check=True, text=True, capture_output=True)
    return float(result.stdout.strip())


def build_sheet(args):
    indices = sorted({
        1,
        max(1, round(args.frame_count * 0.25)),
        max(1, round(args.frame_count * 0.50)),
        max(1, round(args.frame_count * 0.75)),
        args.frame_count,
    })
    composites = []
    duration = background_duration(args.background)
    for frame in indices:
        foreground = Image.open(args.foreground_dir / f"{frame:05d}.png").convert("RGBA")
        if foreground.size != (640, 360):
            foreground = foreground.resize((640, 360), Image.Resampling.LANCZOS)
        moment = min(duration - 0.05, args.background_start_sec + (frame - 1) / 30.0)
        composite = Image.alpha_composite(background_frame(args.background, moment), foreground).convert("RGB")
        draw = ImageDraw.Draw(composite)
        draw.rectangle((0, 0, 105, 22), fill=(0, 0, 0))
        draw.text((5, 4), f"frame {frame}", fill=(255, 255, 255))
        composites.append(composite)
    sheet = Image.new("RGB", (640 * 3, 360 * 2), (24, 24, 24))
    for index, image in enumerate(composites):
        sheet.paste(image, ((index % 3) * 640, (index // 3) * 360))
    sheet_path = args.output.with_suffix(".contact_sheet.jpg")
    sheet_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(sheet_path, quality=90)
    return sheet_path


def infer(model_path, gpu, sheet_path, framing):
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", str(gpu))
    import torch
    from qwen_vl_utils import process_vision_info
    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        str(model_path), dtype=torch.bfloat16, device_map="cuda", attn_implementation="sdpa"
    ).eval()
    processor = AutoProcessor.from_pretrained(str(model_path))
    messages = [{"role": "user", "content": [
        {"type": "image", "image": f"file://{sheet_path}", "max_pixels": 1_200_000},
        {"type": "text", "text": PROMPT.format(framing=framing)},
    ]}]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = processor(
        text=[text], images=image_inputs, videos=video_inputs, padding=True, return_tensors="pt"
    ).to(model.device)
    generated = model.generate(**inputs, max_new_tokens=320, do_sample=False)
    response = processor.batch_decode(
        generated[:, inputs.input_ids.shape[1]:], skip_special_tokens=True
    )[0]
    match = re.search(r"\{.*\}", response, flags=re.S)
    if not match:
        raise RuntimeError(f"VLM did not return JSON: {response[:500]}")
    return json.loads(match.group(0))


def main():
    args = parse_args()
    sheet_path = build_sheet(args)
    result = infer(args.model, args.gpu, sheet_path, args.framing)
    score = float(result.get("score", 0.0))
    if score > 1.0:
        score /= 10.0
    result["score"] = max(0.0, min(1.0, score))
    required = [
        "perspective_consistent", "scale_consistent", "placement_stable",
        "physically_plausible",
    ]
    if args.framing == "fullbody":
        required.append("person_on_valid_surface")
    result["status"] = (
        "passed" if all(bool(result.get(key)) for key in required)
        and float(result.get("score", 0.0)) >= 0.70 else "failed"
    )
    result["contact_sheet"] = str(sheet_path)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
