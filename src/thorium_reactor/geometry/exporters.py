from __future__ import annotations

import json
import os
from dataclasses import dataclass
from math import cos, pi, sin
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont


SVG_COLORS = {
    "uo2": "#c77d1c",
    "zirconium": "#7d8ea0",
    "h2o": "#5bb8ff",
    "fuel_salt": "#f4a259",
    "coolant_salt": "#4f78c4",
    "secondary_salt": "#78b7ff",
    "steam": "#f7f9fc",
    "water": "#4aa3df",
    "offgas": "#b7f0d8",
    "graphite": "#2c313a",
    "graphite_shell": "#3f4856",
    "pipe": "#8ea4ba",
    "insulation": "#f1f4f8",
    "concrete": "#9aa3ad",
    "electric_bus": "#f7d154",
    "turbine_alloy": "#b7c0cc",
    "air": "#9ed8f7",
    "void": "#f6f7fb",
}

MATERIAL_STYLES = {
    "fuel_salt": {"fill": "#f4a259", "edge": "#ffd8a8", "glow": "#ffb870"},
    "coolant_salt": {"fill": "#4f78c4", "edge": "#b8cbff", "glow": "#8ab4ff"},
    "secondary_salt": {"fill": "#78b7ff", "edge": "#d7ecff", "glow": "#93c8ff"},
    "steam": {"fill": "#f7f9fc", "edge": "#ffffff", "glow": "#f3fbff"},
    "water": {"fill": "#4aa3df", "edge": "#c8efff", "glow": "#74c4ef"},
    "offgas": {"fill": "#b7f0d8", "edge": "#e6fff3", "glow": "#91eac4"},
    "graphite": {"fill": "#2c313a", "edge": "#5f6c7b", "glow": "#3d4756"},
    "pipe": {"fill": "#8ea4ba", "edge": "#d8e5f0", "glow": "#9cb0c4"},
    "insulation": {"fill": "#f1f4f8", "edge": "#ffffff", "glow": "#f8fbff"},
    "concrete": {"fill": "#9aa3ad", "edge": "#d8dee5", "glow": "#aeb7c0"},
    "electric_bus": {"fill": "#f7d154", "edge": "#fff0a6", "glow": "#ffe17a"},
    "turbine_alloy": {"fill": "#b7c0cc", "edge": "#edf2f8", "glow": "#c5d0dd"},
    "air": {"fill": "#9ed8f7", "edge": "#d7f3ff", "glow": "#b8eeff"},
    "void": {"fill": "#f6f7fb", "edge": "#f6f7fb", "glow": "#ffffff"},
}

GLTF_MATERIALS = {
    "default": {
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.67, 0.73, 0.80, 1.0],
            "metallicFactor": 0.18,
            "roughnessFactor": 0.52,
        },
        "name": "default",
    },
    "fuel_salt": {
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.956, 0.635, 0.349, 0.82],
            "metallicFactor": 0.02,
            "roughnessFactor": 0.18,
        },
        "emissiveFactor": [0.56, 0.21, 0.05],
        "alphaMode": "BLEND",
        "doubleSided": True,
        "name": "fuel_salt",
    },
    "coolant_salt": {
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.31, 0.47, 0.77, 0.56],
            "metallicFactor": 0.02,
            "roughnessFactor": 0.14,
        },
        "emissiveFactor": [0.03, 0.09, 0.20],
        "alphaMode": "BLEND",
        "doubleSided": True,
        "name": "coolant_salt",
    },
    "secondary_salt": {
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.47, 0.72, 1.0, 0.62],
            "metallicFactor": 0.02,
            "roughnessFactor": 0.16,
        },
        "alphaMode": "BLEND",
        "doubleSided": True,
        "name": "secondary_salt",
    },
    "steam": {
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.96, 0.98, 1.0, 0.5],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.08,
        },
        "alphaMode": "BLEND",
        "doubleSided": True,
        "name": "steam",
    },
    "water": {
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.29, 0.64, 0.87, 0.68],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.14,
        },
        "alphaMode": "BLEND",
        "doubleSided": True,
        "name": "water",
    },
    "offgas": {
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.72, 0.94, 0.85, 0.58],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.18,
        },
        "alphaMode": "BLEND",
        "doubleSided": True,
        "name": "offgas",
    },
    "graphite": {
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.16, 0.18, 0.22, 1.0],
            "metallicFactor": 0.02,
            "roughnessFactor": 0.86,
        },
        "name": "graphite",
    },
    "graphite_shell": {
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.23, 0.27, 0.33, 1.0],
            "metallicFactor": 0.02,
            "roughnessFactor": 0.82,
        },
        "name": "graphite_shell",
    },
    "pipe": {
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.57, 0.64, 0.73, 1.0],
            "metallicFactor": 0.58,
            "roughnessFactor": 0.26,
        },
        "name": "pipe",
    },
    "insulation": {
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.95, 0.96, 0.98, 1.0],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.88,
        },
        "name": "insulation",
    },
    "concrete": {
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.60, 0.64, 0.68, 0.42],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.92,
        },
        "alphaMode": "BLEND",
        "doubleSided": True,
        "name": "concrete",
    },
    "electric_bus": {
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.97, 0.82, 0.33, 1.0],
            "metallicFactor": 0.32,
            "roughnessFactor": 0.34,
        },
        "emissiveFactor": [0.35, 0.24, 0.02],
        "name": "electric_bus",
    },
    "turbine_alloy": {
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.72, 0.75, 0.80, 1.0],
            "metallicFactor": 0.64,
            "roughnessFactor": 0.22,
        },
        "name": "turbine_alloy",
    },
    "air": {
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.73, 0.90, 0.98, 0.09],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.04,
        },
        "alphaMode": "BLEND",
        "doubleSided": True,
        "name": "air",
    },
    "void": {
        "pbrMetallicRoughness": {
            "baseColorFactor": [1.0, 1.0, 1.0, 0.03],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.02,
        },
        "alphaMode": "BLEND",
        "doubleSided": True,
        "name": "void",
    },
}

CUTAWAY_START = pi / 7.0
CUTAWAY_STOP = 2.0 * pi - pi / 5.0
RENDER_SIZE = (1800, 1400)
LIGHT_VECTOR = (0.72, -0.34, 0.61)


@dataclass(slots=True)
class SolidMesh:
    name: str
    vertices: list[tuple[float, float, float]]
    faces: list[tuple[int, int, int]]


def export_geometry(
    description: dict[str, Any],
    output_dir: Path,
    *,
    summary: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    svg_path = output_dir / f"{description['name']}.svg"
    obj_path = output_dir / f"{description['name']}.obj"
    stl_path = output_dir / f"{description['name']}.stl"
    gltf_path = output_dir / f"{description['name']}.gltf"
    png_path = output_dir / f"{description['name']}.png"
    hero_cutaway_path = output_dir / f"{description['name']}_hero_cutaway.png"
    annotated_cutaway_path = output_dir / f"{description['name']}_annotated_cutaway.png"
    physics_overlay_path = output_dir / f"{description['name']}_physics_overlay.png"
    mesh_validation_path = output_dir / f"{description['name']}_mesh_validation.json"
    gif_path = output_dir / f"{description['name']}.gif"
    mp4_path = output_dir / f"{description['name']}.mp4"
    blender_script_path = output_dir / f"{description['name']}_blender_gpu.py"

    svg_path.write_text(render_svg(description), encoding="utf-8")
    obj_path.write_text(render_obj(description), encoding="utf-8")
    stl_path.write_text(render_stl(description), encoding="utf-8")
    mesh_validation_path.write_text(
        json.dumps(validate_watertight_meshes(description), indent=2, sort_keys=True),
        encoding="utf-8",
    )

    assets = {
        "svg": str(svg_path),
        "obj": str(obj_path),
        "stl": str(stl_path),
        "mesh_validation": str(mesh_validation_path),
    }
    rendered_gltf = render_gltf(description, gltf_path)
    if rendered_gltf is not None:
        assets["gltf"] = str(rendered_gltf)
        gltf_bin_path = rendered_gltf.with_suffix(".bin")
        if gltf_bin_path.exists():
            assets["gltf_bin"] = str(gltf_bin_path)
        blender_script = render_blender_gpu_script(description, rendered_gltf, blender_script_path)
        if blender_script is not None:
            assets["blender_gpu_script"] = str(blender_script)
    rendered_png = render_png(description, png_path)
    if rendered_png is not None:
        assets["png"] = str(rendered_png)
        with Image.open(rendered_png) as base_image:
            hero_image = base_image.convert("RGB")
        hero_image.save(hero_cutaway_path)
        assets["hero_cutaway"] = str(hero_cutaway_path)
        _render_annotated_cutaway(hero_image.copy(), annotated_cutaway_path, summary or {}, validation or {})
        _render_physics_overlay(hero_image.copy(), physics_overlay_path, summary or {}, validation or {})
        assets["annotated_cutaway"] = str(annotated_cutaway_path)
        assets["physics_overlay"] = str(physics_overlay_path)
    rendered_gif = render_gif(description, gif_path)
    if rendered_gif is not None:
        assets["gif"] = str(rendered_gif)
    rendered_mp4 = render_mp4(description, mp4_path)
    if rendered_mp4 is not None:
        assets["mp4"] = str(rendered_mp4)
    return assets


def render_svg(description: dict[str, Any]) -> str:
    if description["type"] == "pin":
        return _render_pin_svg(description)
    if description["type"] == "ring_lattice_core":
        return _render_core_svg(description)
    if description["type"] == "detailed_molten_salt_reactor":
        return _render_detailed_reactor_svg(description)
    raise ValueError(f"Unsupported geometry description type: {description['type']}")


def render_obj(description: dict[str, Any]) -> str:
    return _build_obj(_collect_export_solids(description))


def render_stl(description: dict[str, Any]) -> str:
    return _build_stl(_collect_export_solids(description))


def render_png(description: dict[str, Any], output_path: Path) -> Path | None:
    if description["type"] != "detailed_molten_salt_reactor":
        return None
    image = _render_detailed_reactor_frame(description)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output_path)
    return output_path


def render_gif(description: dict[str, Any], output_path: Path) -> Path | None:
    if description["type"] != "detailed_molten_salt_reactor":
        return None
    animation = description.get("animation")
    if not isinstance(animation, dict) or not animation.get("paths"):
        return None

    frame_count = max(int(animation.get("frame_count", 24)), 2)
    fps = max(int(animation.get("fps", 12)), 1)
    frames = [
        _render_detailed_reactor_frame(description, animation_phase=index / frame_count)
        .convert("P", palette=Image.ADAPTIVE, colors=255)
        for index in range(frame_count)
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=max(1, int(round(1000 / fps))),
        loop=0,
        disposal=2,
    )
    return output_path


def render_mp4(description: dict[str, Any], output_path: Path) -> Path | None:
    if description["type"] != "detailed_molten_salt_reactor":
        return None
    animation = description.get("animation")
    if not isinstance(animation, dict) or not animation.get("paths"):
        return None

    ffmpeg_path = _resolve_ffmpeg_binary()
    if ffmpeg_path is None:
        return None

    frame_count = max(int(animation.get("frame_count", 24)), 2)
    fps = max(int(animation.get("fps", 12)), 1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames_root = output_path.parent / f".{output_path.stem}-frames-{time.time_ns()}"
    frames_root.mkdir(parents=True, exist_ok=True)

    try:
        for index in range(frame_count):
            frame_path = frames_root / f"frame_{index:04d}.png"
            _render_detailed_reactor_frame(description, animation_phase=index / frame_count).convert("RGB").save(frame_path)

        frame_pattern = str(frames_root / "frame_%04d.png")
        commands = [
            [
                ffmpeg_path,
                "-y",
                "-framerate",
                str(fps),
                "-i",
                frame_pattern,
                "-vf",
                "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            [
                ffmpeg_path,
                "-y",
                "-framerate",
                str(fps),
                "-i",
                frame_pattern,
                "-vf",
                "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-c:v",
                "mpeg4",
                "-q:v",
                "3",
                str(output_path),
            ],
        ]

        for command in commands:
            completed = subprocess.run(command, capture_output=True, text=True)
            if completed.returncode == 0 and output_path.exists():
                return output_path
    finally:
        _cleanup_directory(frames_root)
    return None


def _render_annotated_cutaway(
    image: Image.Image,
    output_path: Path,
    summary: dict[str, Any],
    validation: dict[str, Any],
) -> None:
    lines = _build_annotation_lines(summary, validation)
    _draw_information_panel(
        image,
        title="Annotated cutaway",
        subtitle="State-linked reactor overview",
        lines=lines,
        accent="#f4a259",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def _render_physics_overlay(
    image: Image.Image,
    output_path: Path,
    summary: dict[str, Any],
    validation: dict[str, Any],
) -> None:
    lines = _build_physics_overlay_lines(summary, validation)
    _draw_information_panel(
        image,
        title="Physics overlay",
        subtitle="Summary, validity, and benchmark state",
        lines=lines,
        accent="#4f78c4",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def _build_annotation_lines(summary: dict[str, Any], validation: dict[str, Any]) -> list[str]:
    validity = summary.get("model_validity", {})
    reduced_order = summary.get("flow", {}).get("reduced_order", {})
    active_flow = reduced_order.get("active_flow", {})
    lines = [
        f"Case: {summary.get('case', 'unknown')}",
        f"Validity: {validity.get('status', 'unknown')}",
        f"Active flow area: {active_flow.get('total_flow_area_cm2', 'n/a')} cm2",
        f"Velocity: {active_flow.get('representative_velocity_m_s', 'n/a')} m/s",
        f"Residence time: {active_flow.get('representative_residence_time_s', 'n/a')} s",
        f"Traceability: {summary.get('benchmark_traceability', {}).get('traceability_score', 'n/a')}",
        f"Validation maturity: {summary.get('validation_maturity', {}).get('validation_maturity_score', 'n/a')}",
    ]
    failed_names = [check.get("name", "") for check in validation.get("checks", []) if check.get("status") == "fail"]
    if failed_names:
        lines.append("Failed checks:")
        lines.extend(f"- {name}" for name in failed_names[:4])
    return lines


def _build_physics_overlay_lines(summary: dict[str, Any], validation: dict[str, Any]) -> list[str]:
    bop = summary.get("bop", {})
    reduced_order = summary.get("flow", {}).get("reduced_order", {})
    active_flow = reduced_order.get("active_flow", {})
    primary_system = summary.get("primary_system", {})
    hydraulics = primary_system.get("loop_hydraulics", {})
    checks = validation.get("checks", [])
    pass_count = sum(1 for check in checks if check.get("status") == "pass")
    fail_count = sum(1 for check in checks if check.get("status") == "fail")
    lines = [
        f"Primary mass flow: {bop.get('primary_mass_flow_kg_s', 'n/a')} kg/s",
        f"Primary delta-T: {bop.get('primary_delta_t_c', 'n/a')} C",
        f"Active channels: {active_flow.get('channel_count', 'n/a')}",
        f"Stagnant channels: {reduced_order.get('stagnant_inventory', {}).get('channel_count', 'n/a')}",
        f"Pump head: {hydraulics.get('pump_head_m', 'n/a')} m",
        f"HX area: {primary_system.get('heat_exchanger', {}).get('required_area_m2', 'n/a')} m2",
        f"Validation pass/fail: {pass_count}/{fail_count}",
    ]
    failed_messages = summary.get("model_validity", {}).get("failed_messages", [])
    if failed_messages:
        lines.append("Model limits:")
        lines.extend(f"- {message}" for message in failed_messages[:3])
    return lines


def _draw_information_panel(
    image: Image.Image,
    *,
    title: str,
    subtitle: str,
    lines: list[str],
    accent: str,
) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    font = ImageFont.load_default()
    x0 = int(image.width * 0.62)
    y0 = int(image.height * 0.08)
    x1 = image.width - 40
    y1 = image.height - 40
    draw.rounded_rectangle(
        (x0, y0, x1, y1),
        radius=26,
        fill=(16, 20, 27, 208),
        outline=ImageColor.getrgb(accent),
        width=3,
    )
    draw.rectangle((x0, y0, x1, y0 + 12), fill=ImageColor.getrgb(accent))
    draw.text((x0 + 24, y0 + 28), title, fill="#ffffff", font=font)
    draw.text((x0 + 24, y0 + 52), subtitle, fill="#cbd5e1", font=font)

    cursor_y = y0 + 96
    line_height = 22
    for line in lines[:18]:
        if line.startswith("- "):
            draw.text((x0 + 38, cursor_y), line, fill="#f8fafc", font=font)
        else:
            draw.text((x0 + 24, cursor_y), line, fill="#f8fafc", font=font)
        cursor_y += line_height


def _resolve_ffmpeg_binary() -> str | None:
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path

    runtime_roots: list[Path] = []
    for env_name in ("PYTBKN_ENV", "CONDA_PREFIX"):
        value = os.environ.get(env_name)
        if value:
            runtime_roots.append(Path(value))

    runtime_roots.append(Path(sys.executable).resolve().parent)

    repo_root = os.environ.get("REPO_ROOT")
    if repo_root:
        runtime_roots.append(Path(repo_root) / ".runtime-env")

    seen: set[Path] = set()
    for root in runtime_roots:
        if root in seen:
            continue
        seen.add(root)
        for candidate in (
            root / "Library" / "bin" / "ffmpeg.exe",
            root / "bin" / "ffmpeg",
            root / "ffmpeg.exe",
        ):
            if candidate.exists():
                return str(candidate)
    return None


def _cleanup_directory(path: Path) -> None:
    for _ in range(5):
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
            return
        except OSError:
            time.sleep(0.1)
    if os.name == "nt" and path.exists():
        subprocess.run(
            ["cmd.exe", "/c", "rmdir", "/s", "/q", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if not path.exists():
            return
    shutil.rmtree(path, ignore_errors=True)


def validate_watertight_meshes(description: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for mesh in _build_meshes(_collect_export_solids(description)):
        edge_counts: dict[tuple[int, int], int] = {}
        for a_index, b_index, c_index in mesh.faces:
            for edge in ((a_index, b_index), (b_index, c_index), (c_index, a_index)):
                key = tuple(sorted(edge))
                edge_counts[key] = edge_counts.get(key, 0) + 1
        open_edges = sum(1 for count in edge_counts.values() if count == 1)
        non_manifold_edges = sum(1 for count in edge_counts.values() if count > 2)
        results.append(
            {
                "name": mesh.name,
                "watertight": open_edges == 0 and non_manifold_edges == 0,
                "open_edges": open_edges,
                "non_manifold_edges": non_manifold_edges,
                "face_count": len(mesh.faces),
                "vertex_count": len(mesh.vertices),
            }
        )
    return results


def _render_detailed_reactor_frame(
    description: dict[str, Any],
    animation_phase: float | None = None,
) -> Image.Image:
    image = Image.new("RGBA", RENDER_SIZE, (4, 10, 18, 255))
    glow_layer = Image.new("RGBA", RENDER_SIZE, (0, 0, 0, 0))
    face_layer = Image.new("RGBA", RENDER_SIZE, (0, 0, 0, 0))
    background = ImageDraw.Draw(image, "RGBA")
    glow_draw = ImageDraw.Draw(glow_layer, "RGBA")
    face_draw = ImageDraw.Draw(face_layer, "RGBA")

    _paint_background(background, image.size)

    scene_bounds = _compute_scene_bounds(description["render_solids"])
    scale, center = _fit_scene_to_canvas(scene_bounds, image.size)

    _draw_ground_rings(background, center, scale, scene_bounds)

    faces = _build_scene_faces(description["render_solids"], scale, center)
    faces.sort(key=lambda face: face["depth"])
    for face in faces:
        if face["glow"] is not None:
            glow_draw.polygon(face["points"], fill=face["glow"])
        face_draw.polygon(face["points"], fill=face["fill"], outline=face["outline"])

    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=18))
    image = Image.alpha_composite(image, glow_layer)
    image = Image.alpha_composite(image, face_layer)

    if animation_phase is not None:
        animation = description.get("animation")
        if isinstance(animation, dict) and animation.get("paths"):
            flow_glow = Image.new("RGBA", RENDER_SIZE, (0, 0, 0, 0))
            flow_layer = Image.new("RGBA", RENDER_SIZE, (0, 0, 0, 0))
            flow_glow_draw = ImageDraw.Draw(flow_glow, "RGBA")
            flow_draw = ImageDraw.Draw(flow_layer, "RGBA")
            _draw_animation_overlay(flow_glow_draw, flow_draw, animation, animation_phase, scale, center)
            flow_glow = flow_glow.filter(ImageFilter.GaussianBlur(radius=12))
            image = Image.alpha_composite(image, flow_glow)
            image = Image.alpha_composite(image, flow_layer)

    return image


def _draw_animation_overlay(
    glow_draw: ImageDraw.ImageDraw,
    flow_draw: ImageDraw.ImageDraw,
    animation: dict[str, Any],
    phase: float,
    scale: float,
    center: tuple[float, float],
) -> None:
    for path in animation.get("paths", []):
        points = [tuple(float(value) for value in point) for point in path.get("points", [])]
        if len(points) < 2:
            continue
        material = str(path.get("material", "fuel_salt"))
        style = MATERIAL_STYLES.get(material, MATERIAL_STYLES["fuel_salt"])
        projected = [_project(x_value, y_value, z_value, scale, center) for x_value, y_value, z_value in points]
        width_px = max(2, int(round(float(path.get("width_cm", 1.0)) * scale * 0.18)))
        glow_alpha = 44 if material == "coolant_salt" else 58
        flow_draw.line(projected, fill=_shade_color(style["edge"], 1.0, 36), width=max(1, width_px - 1))
        glow_draw.line(projected, fill=_shade_color(style["glow"], 1.0, glow_alpha // 2), width=width_px + 6)

        packet_count = max(int(path.get("packet_count", 4)), 1)
        packet_length_cm = max(float(path.get("packet_length_cm", 8.0)), 1.0)
        speed = float(path.get("speed", 0.6))
        phase_offset = float(path.get("phase_offset", 0.0))
        loop = bool(path.get("loop", False))
        total_length = _polyline_total_length(points)
        if total_length <= 0.0:
            continue

        for packet_index in range(packet_count):
            progress = (phase * speed + phase_offset + packet_index / packet_count) % 1.0
            start_distance = progress * total_length
            packet_points = _sample_polyline_window(points, start_distance, packet_length_cm, loop=loop)
            if len(packet_points) < 2:
                continue
            packet_projected = [
                _project(x_value, y_value, z_value, scale, center)
                for x_value, y_value, z_value in packet_points
            ]
            glow_draw.line(packet_projected, fill=_shade_color(style["glow"], 1.0, glow_alpha), width=width_px + 10)
            flow_draw.line(packet_projected, fill=_shade_color(style["edge"], 1.04, 228), width=width_px + 1)
            _draw_particle_head(flow_draw, packet_projected[-1], width_px, style["edge"], 236)


def _draw_particle_head(
    draw: ImageDraw.ImageDraw,
    point: tuple[float, float],
    width_px: int,
    hex_color: str,
    alpha: int,
) -> None:
    radius = max(2, width_px // 2 + 1)
    x_value, y_value = point
    draw.ellipse(
        [x_value - radius, y_value - radius, x_value + radius, y_value + radius],
        fill=_shade_color(hex_color, 1.02, alpha),
    )


def _polyline_total_length(points: list[tuple[float, float, float]]) -> float:
    return sum(_segment_length(start, stop) for start, stop in zip(points, points[1:]))


def _sample_polyline_window(
    points: list[tuple[float, float, float]],
    start_distance: float,
    packet_length_cm: float,
    *,
    loop: bool,
) -> list[tuple[float, float, float]]:
    total_length = _polyline_total_length(points)
    if total_length <= 0.0:
        return []

    capped_length = min(packet_length_cm, total_length if loop else max(total_length - start_distance, 0.0))
    if capped_length <= 0.0:
        return [_sample_polyline_point(points, start_distance % total_length if loop else total_length)]

    sample_count = max(5, int(capped_length / max(total_length / 16.0, 1.0)) + 2)
    distances = [start_distance + capped_length * index / (sample_count - 1) for index in range(sample_count)]
    if loop:
        return [_sample_polyline_point(points, distance % total_length) for distance in distances]
    return [_sample_polyline_point(points, min(distance, total_length)) for distance in distances]


def _sample_polyline_point(
    points: list[tuple[float, float, float]],
    distance: float,
) -> tuple[float, float, float]:
    traversed = 0.0
    for start, stop in zip(points, points[1:]):
        segment_length = _segment_length(start, stop)
        if segment_length <= 0.0:
            continue
        if traversed + segment_length >= distance:
            ratio = (distance - traversed) / segment_length
            return (
                start[0] + (stop[0] - start[0]) * ratio,
                start[1] + (stop[1] - start[1]) * ratio,
                start[2] + (stop[2] - start[2]) * ratio,
            )
        traversed += segment_length
    return points[-1]


def _segment_length(
    start: tuple[float, float, float],
    stop: tuple[float, float, float],
) -> float:
    return (
        (stop[0] - start[0]) ** 2
        + (stop[1] - start[1]) ** 2
        + (stop[2] - start[2]) ** 2
    ) ** 0.5


def _render_pin_svg(description: dict[str, Any]) -> str:
    pitch = description["pitch"]
    canvas = 800
    scale = (canvas * 0.8) / pitch
    half = canvas / 2
    circles: list[str] = []
    for layer in reversed(description["layers"]):
        color = SVG_COLORS.get(layer.get("material", "void"), "#dddddd")
        circles.append(
            f'<circle cx="{half}" cy="{half}" r="{layer["outer_radius"] * scale:.2f}" '
            f'fill="{color}" stroke="#222222" stroke-width="1" />'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas} {canvas}">'
        f'<rect width="{canvas}" height="{canvas}" fill="#ffffff" stroke="#222222" stroke-width="4" />'
        + "".join(circles)
        + "</svg>"
    )


def _render_core_svg(description: dict[str, Any]) -> str:
    pitch = description["pitch"]
    canvas = 1000
    scale = (canvas * 0.8) / pitch
    half = canvas / 2
    elements = [
        f'<rect width="{canvas}" height="{canvas}" fill="#ffffff" stroke="#222222" stroke-width="4" />',
        f'<circle cx="{half}" cy="{half}" r="{description["core_radius"] * scale:.2f}" fill="{SVG_COLORS["graphite"]}" stroke="#000000" stroke-width="2" />',
    ]
    for channel in description["channels"]:
        cx = half + channel["x"] * scale
        cy = half - channel["y"] * scale
        for layer in reversed(channel["layers"]):
            color = SVG_COLORS.get(layer.get("material", "void"), "#dddddd")
            elements.append(
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{layer["outer_radius"] * scale:.2f}" '
                f'fill="{color}" stroke="#222222" stroke-width="0.5" />'
            )
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas} {canvas}">{"".join(elements)}</svg>'


def _render_detailed_reactor_svg(description: dict[str, Any]) -> str:
    pitch = float(description["pitch"])
    canvas = 1400
    half = canvas / 2.0
    scale = (canvas * 0.78) / pitch
    shells = sorted(
        [shell for shell in description["shells"] if shell["z_min"] <= 0.0 <= shell["z_max"]],
        key=lambda shell: shell["outer_radius"],
        reverse=True,
    )
    channel_counts = description["channel_variant_counts"]

    elements = [
        '<defs>',
        '<linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">',
        '<stop offset="0%" stop-color="#08111b" />',
        '<stop offset="100%" stop-color="#112235" />',
        "</linearGradient>",
        "</defs>",
        f'<rect width="{canvas}" height="{canvas}" fill="url(#bg)" rx="48" />',
    ]

    for shell in shells:
        color = SVG_COLORS.get(shell["material"], "#8b96a8")
        elements.append(
            f'<circle cx="{half:.1f}" cy="{half:.1f}" r="{float(shell["outer_radius"]) * scale:.2f}" '
            f'fill="{color}" fill-opacity="{float(shell.get("opacity", 0.3)):.2f}" stroke="#dce8f3" stroke-opacity="0.14" stroke-width="2" />'
        )

    for channel in description["channels"]:
        cx = half + float(channel["x"]) * scale
        cy = half - float(channel["y"]) * scale
        stroke = "#f8fbff" if channel["variant"] == "fuel" else "#8de1ff"
        for layer in reversed(channel["layers"]):
            color = SVG_COLORS.get(layer.get("material", "void"), "#e6edf3")
            elements.append(
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{float(layer["outer_radius"]) * scale:.2f}" '
                f'fill="{color}" fill-opacity="0.88" stroke="{stroke}" stroke-opacity="0.16" stroke-width="0.9" />'
            )

    elements.extend(
        [
            '<g fill="#f6fbff" font-family="Arial, Helvetica, sans-serif">',
            '<text x="72" y="92" font-size="34" font-weight="bold">Detailed Molten Salt Reactor Geometry</text>',
            '<text x="72" y="126" font-size="18" fill="#a9c9dd">OpenMC-ready CSG core with plena, reflector, downcomer, vessel stack, and special channels</text>',
            f'<text x="72" y="176" font-size="18">Fuel channels: {channel_counts.get("fuel", 0)}</text>',
            f'<text x="72" y="204" font-size="18">Control guides: {channel_counts.get("control_guides", 0)}</text>',
            f'<text x="72" y="232" font-size="18">Instrumentation wells: {channel_counts.get("instrumentation_wells", 0)}</text>',
            "</g>",
        ]
    )
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas} {canvas}">{"".join(elements)}</svg>'


def _build_obj(solids: list[dict[str, Any]], sides: int = 28) -> str:
    vertex_lines: list[str] = []
    face_lines: list[str] = []
    vertex_offset = 1

    for mesh in _build_meshes(solids, sides=sides):
        vertex_lines.extend(f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in mesh.vertices)
        face_lines.append(f"o {mesh.name}")
        for a, b, c in mesh.faces:
            face_lines.append(f"f {vertex_offset + a} {vertex_offset + b} {vertex_offset + c}")
        vertex_offset += len(mesh.vertices)

    return "\n".join(["# Procedural reactor geometry export", *vertex_lines, *face_lines]) + "\n"


def _build_stl(solids: list[dict[str, Any]], sides: int = 28) -> str:
    lines = ["solid procedural_reactor_geometry"]
    for mesh in _build_meshes(solids, sides=sides):
        for a, b, c in mesh.faces:
            vertex_a = mesh.vertices[a]
            vertex_b = mesh.vertices[b]
            vertex_c = mesh.vertices[c]
            normal = _triangle_normal(vertex_a, vertex_b, vertex_c)
            lines.append(f"  facet normal {normal[0]:.6e} {normal[1]:.6e} {normal[2]:.6e}")
            lines.append("    outer loop")
            lines.append(f"      vertex {vertex_a[0]:.6f} {vertex_a[1]:.6f} {vertex_a[2]:.6f}")
            lines.append(f"      vertex {vertex_b[0]:.6f} {vertex_b[1]:.6f} {vertex_b[2]:.6f}")
            lines.append(f"      vertex {vertex_c[0]:.6f} {vertex_c[1]:.6f} {vertex_c[2]:.6f}")
            lines.append("    endloop")
            lines.append("  endfacet")
    lines.append("endsolid procedural_reactor_geometry")
    return "\n".join(lines) + "\n"


def _collect_export_solids(description: dict[str, Any]) -> list[dict[str, Any]]:
    if description["type"] == "pin":
        return [
            {
                "name": layer["name"],
                "outer_radius": layer["outer_radius"],
                "inner_radius": layer.get("inner_radius", 0.0),
                "x": 0.0,
                "y": 0.0,
                "z_min": 0.0,
                "z_max": description.get("height", 100.0),
            }
            for layer in description["layers"]
            if layer.get("material")
        ]
    if description["type"] == "ring_lattice_core":
        solids: list[dict[str, Any]] = [
            {
                "name": "core_graphite",
                "outer_radius": description["core_radius"],
                "inner_radius": 0.0,
                "x": 0.0,
                "y": 0.0,
                "z_min": 0.0,
                "z_max": description.get("height", 200.0),
            }
        ]
        for channel in description["channels"]:
            for layer in channel["layers"]:
                if not layer.get("material"):
                    continue
                solids.append(
                    {
                        "name": f"{channel['name']}_{layer['name']}",
                        "outer_radius": layer["outer_radius"],
                        "inner_radius": layer.get("inner_radius", 0.0),
                        "x": channel["x"],
                        "y": channel["y"],
                        "z_min": 0.0,
                        "z_max": description.get("height", 200.0),
                    }
                )
        return solids
    if description["type"] == "detailed_molten_salt_reactor":
        return list(description["render_solids"])
    raise ValueError(f"Unsupported geometry description type: {description['type']}")


def _build_meshes(solids: list[dict[str, Any]], sides: int = 28) -> list[SolidMesh]:
    meshes: list[SolidMesh] = []
    for solid in solids:
        mesh = _build_solid_mesh(solid, sides=sides)
        if mesh is not None:
            meshes.append(mesh)
    return meshes


def _build_solid_mesh(solid: dict[str, Any], sides: int = 28) -> SolidMesh | None:
    if solid.get("type") == "box":
        return _build_box_mesh(solid)
    return _build_cylinder_mesh(solid, sides=sides)


def _build_cylinder_mesh(solid: dict[str, Any], sides: int = 28) -> SolidMesh | None:
    outer_radius = float(solid["outer_radius"])
    inner_radius = float(solid.get("inner_radius", 0.0))
    axis = str(solid.get("axis", "z"))
    axis_min, axis_max, center_a, center_b = _axis_coordinates(solid, axis)
    if outer_radius <= 0.0 or axis_max <= axis_min:
        return None

    theta_values = [2.0 * pi * step / sides for step in range(sides)]

    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    outer_start: list[int] = []
    outer_stop: list[int] = []

    for theta in theta_values:
        outer_start.append(len(vertices))
        vertices.append(_cylinder_point(axis, axis_min, center_a, center_b, outer_radius, theta))
    for theta in theta_values:
        outer_stop.append(len(vertices))
        vertices.append(_cylinder_point(axis, axis_max, center_a, center_b, outer_radius, theta))

    inner_start: list[int] = []
    inner_stop: list[int] = []
    center_start = -1
    center_stop = -1
    if inner_radius > 0.0:
        for theta in theta_values:
            inner_start.append(len(vertices))
            vertices.append(_cylinder_point(axis, axis_min, center_a, center_b, inner_radius, theta))
        for theta in theta_values:
            inner_stop.append(len(vertices))
            vertices.append(_cylinder_point(axis, axis_max, center_a, center_b, inner_radius, theta))
    else:
        center_start = len(vertices)
        vertices.append(_cylinder_center(axis, axis_min, center_a, center_b))
        center_stop = len(vertices)
        vertices.append(_cylinder_center(axis, axis_max, center_a, center_b))

    for step in range(sides):
        next_step = (step + 1) % sides
        faces.append((outer_start[step], outer_start[next_step], outer_stop[step]))
        faces.append((outer_start[next_step], outer_stop[next_step], outer_stop[step]))

        if inner_radius > 0.0:
            faces.append((inner_start[next_step], inner_start[step], inner_stop[step]))
            faces.append((inner_stop[next_step], inner_start[next_step], inner_stop[step]))
            faces.append((outer_start[next_step], outer_start[step], inner_start[step]))
            faces.append((outer_start[next_step], inner_start[step], inner_start[next_step]))
            faces.append((outer_stop[step], outer_stop[next_step], inner_stop[step]))
            faces.append((outer_stop[next_step], inner_stop[next_step], inner_stop[step]))
        else:
            faces.append((outer_start[next_step], outer_start[step], center_start))
            faces.append((outer_stop[step], outer_stop[next_step], center_stop))

    return SolidMesh(name=str(solid["name"]), vertices=vertices, faces=faces)


def _build_box_mesh(solid: dict[str, Any]) -> SolidMesh | None:
    x_min = float(solid["x_min"])
    x_max = float(solid["x_max"])
    y_min = float(solid["y_min"])
    y_max = float(solid["y_max"])
    z_min = float(solid["z_min"])
    z_max = float(solid["z_max"])
    if x_max <= x_min or y_max <= y_min or z_max <= z_min:
        return None

    vertices = [
        (x_min, y_min, z_min),
        (x_max, y_min, z_min),
        (x_max, y_max, z_min),
        (x_min, y_max, z_min),
        (x_min, y_min, z_max),
        (x_max, y_min, z_max),
        (x_max, y_max, z_max),
        (x_min, y_max, z_max),
    ]
    faces = [
        (0, 1, 2),
        (0, 2, 3),
        (4, 6, 5),
        (4, 7, 6),
        (0, 4, 5),
        (0, 5, 1),
        (1, 5, 6),
        (1, 6, 2),
        (2, 6, 7),
        (2, 7, 3),
        (3, 7, 4),
        (3, 4, 0),
    ]
    return SolidMesh(name=str(solid["name"]), vertices=vertices, faces=faces)


def _triangle_normal(
    vertex_a: tuple[float, float, float],
    vertex_b: tuple[float, float, float],
    vertex_c: tuple[float, float, float],
) -> tuple[float, float, float]:
    ax, ay, az = vertex_a
    bx, by, bz = vertex_b
    cx, cy, cz = vertex_c
    edge_ab = (bx - ax, by - ay, bz - az)
    edge_ac = (cx - ax, cy - ay, cz - az)
    normal = (
        edge_ab[1] * edge_ac[2] - edge_ab[2] * edge_ac[1],
        edge_ab[2] * edge_ac[0] - edge_ab[0] * edge_ac[2],
        edge_ab[0] * edge_ac[1] - edge_ab[1] * edge_ac[0],
    )
    magnitude = (normal[0] ** 2 + normal[1] ** 2 + normal[2] ** 2) ** 0.5
    if magnitude == 0.0:
        return (0.0, 0.0, 0.0)
    return (normal[0] / magnitude, normal[1] / magnitude, normal[2] / magnitude)


def _paint_background(draw: ImageDraw.ImageDraw, size: tuple[int, int]) -> None:
    width, height = size
    top = ImageColor.getrgb("#07111b")
    bottom = ImageColor.getrgb("#12304a")
    for row in range(height):
        blend = row / max(height - 1, 1)
        color = tuple(int(top[channel] + (bottom[channel] - top[channel]) * blend) for channel in range(3))
        draw.line([(0, row), (width, row)], fill=color)

    center_x = width * 0.52
    center_y = height * 0.36
    for radius in range(540, 80, -18):
        alpha = max(0, int(28 - radius / 30))
        if alpha <= 0:
            continue
        bounds = [
            center_x - radius,
            center_y - radius * 0.8,
            center_x + radius,
            center_y + radius * 0.8,
        ]
        draw.ellipse(bounds, fill=(74, 138, 179, alpha))


def _compute_scene_bounds(solids: list[dict[str, Any]]) -> dict[str, float]:
    x_values: list[float] = []
    y_values: list[float] = []
    z_values: list[float] = []
    for solid in solids:
        bounds = _solid_bounds(solid)
        x_values.extend((bounds["x_min"], bounds["x_max"]))
        y_values.extend((bounds["y_min"], bounds["y_max"]))
        z_values.extend((bounds["z_min"], bounds["z_max"]))
    if not x_values:
        return {"x_min": -1.0, "x_max": 1.0, "y_min": -1.0, "y_max": 1.0, "z_min": -1.0, "z_max": 1.0}
    return {
        "x_min": min(x_values),
        "x_max": max(x_values),
        "y_min": min(y_values),
        "y_max": max(y_values),
        "z_min": min(z_values),
        "z_max": max(z_values),
    }


def _fit_scene_to_canvas(bounds: dict[str, float], size: tuple[int, int]) -> tuple[float, tuple[float, float]]:
    width, height = size
    corners = [
        (x, y, z)
        for x in (bounds["x_min"], bounds["x_max"])
        for y in (bounds["y_min"], bounds["y_max"])
        for z in (bounds["z_min"], bounds["z_max"])
    ]
    projected = [_project(x, y, z, 1.0, (0.0, 0.0)) for x, y, z in corners]
    x_min = min(point[0] for point in projected)
    x_max = max(point[0] for point in projected)
    y_min = min(point[1] for point in projected)
    y_max = max(point[1] for point in projected)

    margin_x = width * 0.12
    margin_y = height * 0.08
    available_width = max(width - 2.0 * margin_x, 1.0)
    available_height = max(height - 2.0 * margin_y, 1.0)
    span_x = max(x_max - x_min, 1.0)
    span_y = max(y_max - y_min, 1.0)
    scale = min(available_width / span_x, available_height / span_y)
    center = (margin_x - x_min * scale, margin_y - y_min * scale)
    return scale, center


def _draw_ground_rings(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    scale: float,
    scene_bounds: dict[str, float],
) -> None:
    outer_radius = max(
        abs(scene_bounds["x_min"]),
        abs(scene_bounds["x_max"]),
        abs(scene_bounds["y_min"]),
        abs(scene_bounds["y_max"]),
    )
    z_min = scene_bounds["z_min"]
    for factor, alpha in ((1.0, 48), (0.78, 28), (0.56, 22)):
        ring = []
        radius = outer_radius * factor
        for step in range(80):
            theta = 2.0 * pi * step / 80.0
            ring.append(_project(radius * cos(theta), radius * sin(theta), z_min - 6.0, scale, center))
        draw.line(ring + [ring[0]], fill=(130, 182, 209, alpha), width=2)


def _build_scene_faces(
    solids: list[dict[str, Any]],
    scale: float,
    center: tuple[float, float],
) -> list[dict[str, Any]]:
    faces: list[dict[str, Any]] = []
    ordered_solids = sorted(solids, key=_solid_depth_key, reverse=True)
    for solid in ordered_solids:
        material = solid.get("material")
        if material is None:
            continue
        style = MATERIAL_STYLES.get(material, MATERIAL_STYLES["pipe"])
        if solid.get("type") == "box":
            faces.extend(_build_box_faces(solid, material, style, scale, center))
            continue
        faces.extend(_build_cylinder_faces(solid, material, style, scale, center))
    return faces


def _build_box_faces(
    solid: dict[str, Any],
    material: str,
    style: dict[str, str],
    scale: float,
    center: tuple[float, float],
) -> list[dict[str, Any]]:
    x_min = float(solid["x_min"])
    x_max = float(solid["x_max"])
    y_min = float(solid["y_min"])
    y_max = float(solid["y_max"])
    z_min = float(solid["z_min"])
    z_max = float(solid["z_max"])
    corners = {
        "000": (x_min, y_min, z_min),
        "100": (x_max, y_min, z_min),
        "110": (x_max, y_max, z_min),
        "010": (x_min, y_max, z_min),
        "001": (x_min, y_min, z_max),
        "101": (x_max, y_min, z_max),
        "111": (x_max, y_max, z_max),
        "011": (x_min, y_max, z_max),
    }
    specs = [
        (["001", "101", "111", "011"], (0.0, 0.0, 1.0)),
        (["000", "010", "110", "100"], (0.0, 0.0, -1.0)),
        (["000", "001", "011", "010"], (-1.0, 0.0, 0.0)),
        (["100", "110", "111", "101"], (1.0, 0.0, 0.0)),
        (["010", "011", "111", "110"], (0.0, 1.0, 0.0)),
        (["000", "100", "101", "001"], (0.0, -1.0, 0.0)),
    ]
    faces: list[dict[str, Any]] = []
    for labels, normal in specs:
        shade = _shade_from_normal(normal)
        faces.append(
            _face_entry(
                [corners[label] for label in labels],
                scale,
                center,
                _shade_color(style["fill"], shade, _scaled_alpha(solid, 225)),
                _shade_color(style["edge"], min(1.0, shade + 0.08), _scaled_alpha(solid, 185)),
                _maybe_glow(style["glow"], material, _glow_alpha(material, solid)),
            )
        )
    return faces


def _build_cylinder_faces(
    solid: dict[str, Any],
    material: str,
    style: dict[str, str],
    scale: float,
    center: tuple[float, float],
) -> list[dict[str, Any]]:
    outer_radius = float(solid["outer_radius"])
    inner_radius = float(solid.get("inner_radius", 0.0))
    axis = str(solid.get("axis", "z"))
    cutaway = axis == "z" and bool(solid.get("cutaway"))
    theta_start = float(solid.get("cutaway_start", CUTAWAY_START)) if cutaway else 0.0
    theta_stop = float(solid.get("cutaway_stop", CUTAWAY_STOP)) if cutaway else 2.0 * pi
    axis_min, axis_max, center_a, center_b = _axis_coordinates(solid, axis)
    segments = 42 if outer_radius > 8.0 else 22
    theta_values = [theta_start + (theta_stop - theta_start) * index / segments for index in range(segments + 1)]

    outer_start = [_cylinder_point(axis, axis_min, center_a, center_b, outer_radius, theta) for theta in theta_values]
    outer_stop = [_cylinder_point(axis, axis_max, center_a, center_b, outer_radius, theta) for theta in theta_values]
    if inner_radius > 0.0:
        inner_start = [_cylinder_point(axis, axis_min, center_a, center_b, inner_radius, theta) for theta in theta_values]
        inner_stop = [_cylinder_point(axis, axis_max, center_a, center_b, inner_radius, theta) for theta in theta_values]
    else:
        start_center = _cylinder_center(axis, axis_min, center_a, center_b)
        stop_center = _cylinder_center(axis, axis_max, center_a, center_b)
        inner_start = [start_center] * (segments + 1)
        inner_stop = [stop_center] * (segments + 1)

    faces: list[dict[str, Any]] = []
    for index in range(segments):
        theta_mid = (theta_values[index] + theta_values[index + 1]) / 2.0
        outer_normal = _radial_normal(axis, theta_mid)
        outer_shade = _shade_from_normal(outer_normal)
        faces.append(
            _face_entry(
                [outer_stop[index], outer_stop[index + 1], outer_start[index + 1], outer_start[index]],
                scale,
                center,
                _shade_color(style["fill"], outer_shade, _scaled_alpha(solid, 235)),
                _shade_color(style["edge"], min(1.0, outer_shade + 0.08), _scaled_alpha(solid, 182)),
                _maybe_glow(style["glow"], material, _glow_alpha(material, solid)),
            )
        )

        if inner_radius > 0.0:
            inner_normal = tuple(-value for value in outer_normal)
            inner_shade = _shade_from_normal(inner_normal) * 0.92
            faces.append(
                _face_entry(
                    [inner_stop[index + 1], inner_stop[index], inner_start[index], inner_start[index + 1]],
                    scale,
                    center,
                    _shade_color(style["fill"], inner_shade, _scaled_alpha(solid, 205)),
                    None,
                    None,
                )
            )

    end_specs = [
        (outer_start + list(reversed(inner_start)), _axis_normal(axis, positive=False)),
        (outer_stop + list(reversed(inner_stop)), _axis_normal(axis, positive=True)),
    ]
    for polygon, normal in end_specs:
        shade = _shade_from_normal(normal)
        faces.append(
            _face_entry(
                polygon,
                scale,
                center,
                _shade_color(style["fill"], shade, _scaled_alpha(solid, 220)),
                _shade_color(style["edge"], min(1.0, shade + 0.06), _scaled_alpha(solid, 196)),
                _maybe_glow(style["glow"], material, _glow_alpha(material, solid)),
            )
        )

    if cutaway and inner_radius > 0.0:
        for theta_value in (theta_start, theta_stop):
            cut_normal = _cutaway_normal(theta_value)
            shade = _shade_from_normal(cut_normal) * 0.95
            faces.append(
                _face_entry(
                    [
                        _cylinder_point(axis, axis_max, center_a, center_b, inner_radius, theta_value),
                        _cylinder_point(axis, axis_max, center_a, center_b, outer_radius, theta_value),
                        _cylinder_point(axis, axis_min, center_a, center_b, outer_radius, theta_value),
                        _cylinder_point(axis, axis_min, center_a, center_b, inner_radius, theta_value),
                    ],
                    scale,
                    center,
                    _shade_color(style["fill"], shade, _scaled_alpha(solid, 214)),
                    _shade_color(style["edge"], min(1.0, shade + 0.08), _scaled_alpha(solid, 164)),
                    None,
                )
            )
    return faces


def _solid_depth_key(solid: dict[str, Any]) -> tuple[float, float]:
    bounds = _solid_bounds(solid)
    depth = (
        (bounds["x_min"] + bounds["x_max"]) * 0.5
        + (bounds["y_min"] + bounds["y_max"]) * 0.5
        + (bounds["z_min"] + bounds["z_max"]) * 0.09
    )
    size = (bounds["x_max"] - bounds["x_min"]) + (bounds["y_max"] - bounds["y_min"]) + (bounds["z_max"] - bounds["z_min"])
    return (depth, size)


def _scaled_alpha(solid: dict[str, Any], base_alpha: int) -> int:
    opacity = max(0.0, min(1.0, float(solid.get("opacity", 1.0))))
    return max(0, min(255, int(base_alpha * opacity)))


def _glow_alpha(material: str, solid: dict[str, Any]) -> int:
    if material in {"fuel_salt", "coolant_salt", "secondary_salt", "steam", "water", "offgas", "electric_bus"}:
        return _scaled_alpha(solid, 72)
    if material == "air":
        return _scaled_alpha(solid, 32)
    return 0


def _shade_from_normal(normal: tuple[float, float, float]) -> float:
    dot = max(0.0, normal[0] * LIGHT_VECTOR[0] + normal[1] * LIGHT_VECTOR[1] + normal[2] * LIGHT_VECTOR[2])
    return 0.52 + 0.44 * dot


def _axis_coordinates(solid: dict[str, Any], axis: str) -> tuple[float, float, float, float]:
    if axis == "x":
        return (
            float(solid["x_min"]),
            float(solid["x_max"]),
            float(solid.get("y", 0.0)),
            float(solid.get("z", 0.0)),
        )
    if axis == "y":
        return (
            float(solid["y_min"]),
            float(solid["y_max"]),
            float(solid.get("x", 0.0)),
            float(solid.get("z", 0.0)),
        )
    return (
        float(solid.get("z_min", 0.0)),
        float(solid.get("z_max", float(solid.get("height", 0.0)))),
        float(solid.get("x", 0.0)),
        float(solid.get("y", 0.0)),
    )


def _cylinder_point(
    axis: str,
    axis_value: float,
    center_a: float,
    center_b: float,
    radius: float,
    theta: float,
) -> tuple[float, float, float]:
    if axis == "x":
        return (axis_value, center_a + radius * cos(theta), center_b + radius * sin(theta))
    if axis == "y":
        return (center_a + radius * cos(theta), axis_value, center_b + radius * sin(theta))
    return (center_a + radius * cos(theta), center_b + radius * sin(theta), axis_value)


def _cylinder_center(axis: str, axis_value: float, center_a: float, center_b: float) -> tuple[float, float, float]:
    if axis == "x":
        return (axis_value, center_a, center_b)
    if axis == "y":
        return (center_a, axis_value, center_b)
    return (center_a, center_b, axis_value)


def _axis_normal(axis: str, positive: bool) -> tuple[float, float, float]:
    sign = 1.0 if positive else -1.0
    if axis == "x":
        return (sign, 0.0, 0.0)
    if axis == "y":
        return (0.0, sign, 0.0)
    return (0.0, 0.0, sign)


def _radial_normal(axis: str, theta: float) -> tuple[float, float, float]:
    if axis == "x":
        return (0.0, cos(theta), sin(theta))
    if axis == "y":
        return (cos(theta), 0.0, sin(theta))
    return (cos(theta), sin(theta), 0.0)


def _cutaway_normal(theta: float) -> tuple[float, float, float]:
    return (-sin(theta), cos(theta), 0.12)


def _solid_bounds(solid: dict[str, Any]) -> dict[str, float]:
    if solid.get("type") == "box":
        return {
            "x_min": float(solid["x_min"]),
            "x_max": float(solid["x_max"]),
            "y_min": float(solid["y_min"]),
            "y_max": float(solid["y_max"]),
            "z_min": float(solid["z_min"]),
            "z_max": float(solid["z_max"]),
        }

    outer_radius = float(solid["outer_radius"])
    axis = str(solid.get("axis", "z"))
    if axis == "x":
        return {
            "x_min": float(solid["x_min"]),
            "x_max": float(solid["x_max"]),
            "y_min": float(solid.get("y", 0.0)) - outer_radius,
            "y_max": float(solid.get("y", 0.0)) + outer_radius,
            "z_min": float(solid.get("z", 0.0)) - outer_radius,
            "z_max": float(solid.get("z", 0.0)) + outer_radius,
        }
    if axis == "y":
        return {
            "x_min": float(solid.get("x", 0.0)) - outer_radius,
            "x_max": float(solid.get("x", 0.0)) + outer_radius,
            "y_min": float(solid["y_min"]),
            "y_max": float(solid["y_max"]),
            "z_min": float(solid.get("z", 0.0)) - outer_radius,
            "z_max": float(solid.get("z", 0.0)) + outer_radius,
        }
    return {
        "x_min": float(solid.get("x", 0.0)) - outer_radius,
        "x_max": float(solid.get("x", 0.0)) + outer_radius,
        "y_min": float(solid.get("y", 0.0)) - outer_radius,
        "y_max": float(solid.get("y", 0.0)) + outer_radius,
        "z_min": float(solid.get("z_min", 0.0)),
        "z_max": float(solid.get("z_max", float(solid.get("height", 0.0)))),
    }


def _face_entry(
    polygon_3d: list[tuple[float, float, float]],
    scale: float,
    center: tuple[float, float],
    fill: tuple[int, int, int, int],
    outline: tuple[int, int, int, int] | None,
    glow: tuple[int, int, int, int] | None,
) -> dict[str, Any]:
    depth = sum(point[0] + point[1] + point[2] * 0.18 for point in polygon_3d) / len(polygon_3d)
    return {
        "depth": depth,
        "points": [_project(point[0], point[1], point[2], scale, center) for point in polygon_3d],
        "fill": fill,
        "outline": outline,
        "glow": glow,
    }


def _project(
    x: float,
    y: float,
    z: float,
    scale: float,
    center: tuple[float, float],
) -> tuple[float, float]:
    screen_x = center[0] + (x - y) * 0.88 * scale
    screen_y = center[1] + (x + y) * 0.36 * scale - z * 0.74 * scale
    return (screen_x, screen_y)


def _shade_color(hex_color: str, factor: float, alpha: int) -> tuple[int, int, int, int]:
    red, green, blue = ImageColor.getrgb(hex_color)
    shaded = (
        max(0, min(255, int(red * factor))),
        max(0, min(255, int(green * factor))),
        max(0, min(255, int(blue * factor))),
        alpha,
    )
    return shaded


def _maybe_glow(hex_color: str, material: str, alpha: int) -> tuple[int, int, int, int] | None:
    if alpha <= 0 or material not in {"fuel_salt", "coolant_salt", "secondary_salt", "steam", "water", "offgas", "electric_bus", "air"}:
        return None
    red, green, blue = ImageColor.getrgb(hex_color)
    return (red, green, blue, alpha)


def render_gltf(description: dict[str, Any], output_path: Path) -> Path | None:
    solids = _collect_export_solids(description)
    if not solids:
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sides = 64 if description.get("type") == "detailed_molten_salt_reactor" else 40
    materials, material_lookup = _build_gltf_materials()

    binary = bytearray()
    buffer_views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    meshes: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    for solid in solids:
        material_name = str(solid.get("material") or "default")
        mesh = _build_solid_mesh(solid, sides=sides)
        if mesh is None:
            continue
        primitive = _build_gltf_primitive(binary, buffer_views, accessors, mesh)
        primitive["material"] = material_lookup.get(material_name, material_lookup["default"])
        meshes.append({"name": mesh.name, "primitives": [primitive]})
        nodes.append({"name": mesh.name, "mesh": len(meshes) - 1})

    if not meshes:
        return None

    _pad_binary(binary)
    binary_path = output_path.with_suffix(".bin")
    binary_path.write_bytes(bytes(binary))

    scene_extras = _build_gltf_scene_extras(description, solids)
    payload = {
        "asset": {
            "version": "2.0",
            "generator": "thorium-reactor procedural exporter",
        },
        "scene": 0,
        "scenes": [
            {
                "name": str(description.get("name", "reactor_scene")),
                "nodes": list(range(len(nodes))),
                "extras": scene_extras,
            }
        ],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "buffers": [
            {
                "byteLength": len(binary),
                "uri": binary_path.name,
            }
        ],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return output_path


def render_blender_gpu_script(
    description: dict[str, Any],
    gltf_path: Path,
    output_path: Path,
) -> Path | None:
    if not gltf_path.exists():
        return None

    case_name = str(description.get("name", "reactor_scene"))
    script = f'''# Auto-generated by thorium-reactor.
# Run with: blender --background --python "{output_path.name}"

import bpy
from mathutils import Vector
from pathlib import Path

CASE_NAME = {case_name!r}
EXPORTS_DIR = Path(__file__).resolve().parent
GLTF_PATH = EXPORTS_DIR / {gltf_path.name!r}
OUTPUT_PATH = EXPORTS_DIR / {case_name + '_cycles.png'!r}


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.cameras, bpy.data.lights):
        for block in list(datablocks):
            if block.users == 0:
                datablocks.remove(block)


def enable_gpu() -> str:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 256
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.use_denoising = True
    scene.cycles.device = "CPU"

    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.get_devices()
        for compute_type in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
            try:
                prefs.compute_device_type = compute_type
                prefs.get_devices()
                accelerated = False
                for device in getattr(prefs, "devices", []):
                    device.use = True
                    if getattr(device, "type", "CPU") != "CPU":
                        accelerated = True
                if accelerated:
                    scene.cycles.device = "GPU"
                    return compute_type
            except Exception:
                continue
    except Exception:
        pass
    return "CPU"


def set_world() -> None:
    world = bpy.data.worlds.get("World")
    if world is None:
        world = bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs[0].default_value = (0.012, 0.020, 0.030, 1.0)
        background.inputs[1].default_value = 0.75


def scene_bounds() -> tuple[Vector, Vector]:
    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not mesh_objects:
        return Vector((-1.0, -1.0, -1.0)), Vector((1.0, 1.0, 1.0))
    coords = []
    for obj in mesh_objects:
        for corner in obj.bound_box:
            coords.append(obj.matrix_world @ Vector(corner))
    min_corner = Vector((min(p.x for p in coords), min(p.y for p in coords), min(p.z for p in coords)))
    max_corner = Vector((max(p.x for p in coords), max(p.y for p in coords), max(p.z for p in coords)))
    return min_corner, max_corner


def look_at(obj, target: Vector) -> None:
    direction = target - obj.location
    if direction.length == 0:
        return
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def add_area_light(name: str, location: Vector, target: Vector, *, energy: float, size: float) -> None:
    light_data = bpy.data.lights.new(name=name, type="AREA")
    light_data.energy = energy
    light_data.shape = "DISK"
    light_data.size = size
    light_object = bpy.data.objects.new(name, light_data)
    bpy.context.collection.objects.link(light_object)
    light_object.location = location
    look_at(light_object, target)


def setup_camera_and_lights() -> None:
    min_corner, max_corner = scene_bounds()
    center = (min_corner + max_corner) * 0.5
    extent = max((max_corner - min_corner).length, 1.0)

    bpy.ops.object.camera_add(location=(center.x + extent * 1.45, center.y - extent * 1.2, center.z + extent * 0.72))
    camera = bpy.context.object
    camera.data.lens = 46
    camera.data.dof.use_dof = True
    camera.data.dof.focus_distance = max((center - camera.location).length, 0.1)
    look_at(camera, center)
    bpy.context.scene.camera = camera

    add_area_light(
        "KeyLight",
        Vector((center.x + extent * 1.6, center.y - extent * 1.8, center.z + extent * 1.2)),
        center,
        energy=7000.0,
        size=extent * 0.45,
    )
    add_area_light(
        "FillLight",
        Vector((center.x - extent * 1.1, center.y + extent * 0.7, center.z + extent * 0.8)),
        center,
        energy=2600.0,
        size=extent * 0.7,
    )
    add_area_light(
        "RimLight",
        Vector((center.x - extent * 0.4, center.y - extent * 1.6, center.z + extent * 1.5)),
        center,
        energy=1900.0,
        size=extent * 0.55,
    )


def configure_render() -> None:
    scene = bpy.context.scene
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.film_transparent = False
    scene.cycles.max_bounces = 10
    scene.cycles.diffuse_bounces = 4
    scene.cycles.glossy_bounces = 4
    scene.cycles.transparent_max_bounces = 10
    scene.render.filepath = str(OUTPUT_PATH)


clear_scene()
set_world()
bpy.ops.import_scene.gltf(filepath=str(GLTF_PATH))
backend = enable_gpu()
setup_camera_and_lights()
configure_render()
print(f"Rendering {{CASE_NAME}} with backend: {{backend}}")
bpy.ops.render.render(write_still=True)
print(f"Saved render to {{OUTPUT_PATH}}")
'''
    output_path.write_text(script, encoding="utf-8")
    return output_path


def _build_gltf_materials() -> tuple[list[dict[str, Any]], dict[str, int]]:
    materials: list[dict[str, Any]] = []
    lookup: dict[str, int] = {}
    for material_name, payload in GLTF_MATERIALS.items():
        lookup[material_name] = len(materials)
        materials.append(json.loads(json.dumps(payload)))
    return materials, lookup


def _build_gltf_primitive(
    binary: bytearray,
    buffer_views: list[dict[str, Any]],
    accessors: list[dict[str, Any]],
    mesh: SolidMesh,
) -> dict[str, Any]:
    positions, normals, indices, minima, maxima = _flatten_mesh_for_gltf(mesh)
    position_accessor = _append_float_accessor(
        binary,
        buffer_views,
        accessors,
        positions,
        accessor_type="VEC3",
        minima=minima,
        maxima=maxima,
    )
    normal_accessor = _append_float_accessor(
        binary,
        buffer_views,
        accessors,
        normals,
        accessor_type="VEC3",
    )
    index_accessor = _append_uint_accessor(
        binary,
        buffer_views,
        accessors,
        indices,
    )
    return {
        "attributes": {
            "POSITION": position_accessor,
            "NORMAL": normal_accessor,
        },
        "indices": index_accessor,
        "mode": 4,
    }


def _flatten_mesh_for_gltf(
    mesh: SolidMesh,
) -> tuple[list[float], list[float], list[int], list[float], list[float]]:
    positions: list[float] = []
    normals: list[float] = []
    indices: list[int] = []
    minima = [float("inf"), float("inf"), float("inf")]
    maxima = [float("-inf"), float("-inf"), float("-inf")]
    vertex_index = 0

    for a_index, b_index, c_index in mesh.faces:
        source_a = mesh.vertices[a_index]
        source_b = mesh.vertices[b_index]
        source_c = mesh.vertices[c_index]
        vertices = [
            _to_gltf_coords(source_a),
            _to_gltf_coords(source_b),
            _to_gltf_coords(source_c),
        ]
        normal = _to_gltf_normal(_triangle_normal(source_a, source_b, source_c))
        for vertex in vertices:
            positions.extend(vertex)
            for axis, value in enumerate(vertex):
                minima[axis] = min(minima[axis], value)
                maxima[axis] = max(maxima[axis], value)
        normals.extend((*normal, *normal, *normal))
        indices.extend((vertex_index, vertex_index + 1, vertex_index + 2))
        vertex_index += 3

    return positions, normals, indices, minima, maxima


def _append_float_accessor(
    binary: bytearray,
    buffer_views: list[dict[str, Any]],
    accessors: list[dict[str, Any]],
    values: list[float],
    *,
    accessor_type: str,
    minima: list[float] | None = None,
    maxima: list[float] | None = None,
) -> int:
    _pad_binary(binary)
    byte_offset = len(binary)
    binary.extend(struct.pack(f"<{len(values)}f", *values))
    buffer_view_index = len(buffer_views)
    buffer_views.append(
        {
            "buffer": 0,
            "byteOffset": byte_offset,
            "byteLength": len(binary) - byte_offset,
        }
    )
    accessor_index = len(accessors)
    accessor: dict[str, Any] = {
        "bufferView": buffer_view_index,
        "componentType": 5126,
        "count": len(values) // _accessor_width(accessor_type),
        "type": accessor_type,
    }
    if minima is not None:
        accessor["min"] = minima
    if maxima is not None:
        accessor["max"] = maxima
    accessors.append(accessor)
    return accessor_index


def _append_uint_accessor(
    binary: bytearray,
    buffer_views: list[dict[str, Any]],
    accessors: list[dict[str, Any]],
    values: list[int],
) -> int:
    _pad_binary(binary)
    byte_offset = len(binary)
    binary.extend(struct.pack(f"<{len(values)}I", *values))
    buffer_view_index = len(buffer_views)
    buffer_views.append(
        {
            "buffer": 0,
            "byteOffset": byte_offset,
            "byteLength": len(binary) - byte_offset,
        }
    )
    accessor_index = len(accessors)
    accessors.append(
        {
            "bufferView": buffer_view_index,
            "componentType": 5125,
            "count": len(values),
            "type": "SCALAR",
        }
    )
    return accessor_index


def _accessor_width(accessor_type: str) -> int:
    return {
        "SCALAR": 1,
        "VEC2": 2,
        "VEC3": 3,
        "VEC4": 4,
    }.get(accessor_type, 1)


def _pad_binary(binary: bytearray) -> None:
    while len(binary) % 4:
        binary.append(0)


def _to_gltf_coords(vertex: tuple[float, float, float]) -> tuple[float, float, float]:
    x_value, y_value, z_value = vertex
    return (x_value * 0.01, z_value * 0.01, -y_value * 0.01)


def _to_gltf_normal(normal: tuple[float, float, float]) -> tuple[float, float, float]:
    x_value, y_value, z_value = normal
    return (x_value, z_value, -y_value)


def _build_gltf_scene_extras(description: dict[str, Any], solids: list[dict[str, Any]]) -> dict[str, Any]:
    bounds = _compute_scene_bounds(solids)
    animation = description.get("animation")
    animation_path_count = 0
    if isinstance(animation, dict):
        animation_path_count = len(animation.get("paths", []))
    extras = {
        "source_type": str(description.get("type", "unknown")),
        "render_layout": description.get("render_layout"),
        "animation_path_count": animation_path_count,
        "bounds_cm": {key: round(float(value), 6) for key, value in bounds.items()},
    }
    plant_system = description.get("plant_system")
    if isinstance(plant_system, dict):
        extras["plant_system"] = {
            "type": plant_system.get("type"),
            "basis": plant_system.get("basis"),
            "component_count": len(plant_system.get("components", [])),
            "network_count": len(plant_system.get("networks", [])),
        }
    return extras
