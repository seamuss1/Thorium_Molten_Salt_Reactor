from __future__ import annotations

from dataclasses import dataclass
from math import cos, pi, sin
from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageFilter


SVG_COLORS = {
    "uo2": "#c77d1c",
    "zirconium": "#7d8ea0",
    "h2o": "#5bb8ff",
    "fuel_salt": "#f4a259",
    "graphite": "#2c313a",
    "graphite_shell": "#3f4856",
    "pipe": "#8ea4ba",
    "air": "#9ed8f7",
    "void": "#f6f7fb",
}

MATERIAL_STYLES = {
    "fuel_salt": {"fill": "#f4a259", "edge": "#ffd8a8", "glow": "#ffb870"},
    "graphite": {"fill": "#2c313a", "edge": "#5f6c7b", "glow": "#3d4756"},
    "pipe": {"fill": "#8ea4ba", "edge": "#d8e5f0", "glow": "#9cb0c4"},
    "air": {"fill": "#9ed8f7", "edge": "#d7f3ff", "glow": "#b8eeff"},
    "void": {"fill": "#f6f7fb", "edge": "#f6f7fb", "glow": "#ffffff"},
}

CUTAWAY_START = pi / 7.0
CUTAWAY_STOP = 2.0 * pi - pi / 5.0
RENDER_SIZE = (1800, 1400)


@dataclass(slots=True)
class SolidMesh:
    name: str
    vertices: list[tuple[float, float, float]]
    faces: list[tuple[int, int, int]]


def export_geometry(description: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    svg_path = output_dir / f"{description['name']}.svg"
    obj_path = output_dir / f"{description['name']}.obj"
    stl_path = output_dir / f"{description['name']}.stl"
    png_path = output_dir / f"{description['name']}.png"

    svg_path.write_text(render_svg(description), encoding="utf-8")
    obj_path.write_text(render_obj(description), encoding="utf-8")
    stl_path.write_text(render_stl(description), encoding="utf-8")

    assets = {"svg": str(svg_path), "obj": str(obj_path), "stl": str(stl_path)}
    rendered_png = render_png(description, png_path)
    if rendered_png is not None:
        assets["png"] = str(rendered_png)
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

    image = Image.new("RGBA", RENDER_SIZE, (4, 10, 18, 255))
    glow_layer = Image.new("RGBA", RENDER_SIZE, (0, 0, 0, 0))
    face_layer = Image.new("RGBA", RENDER_SIZE, (0, 0, 0, 0))
    background = ImageDraw.Draw(image, "RGBA")
    glow_draw = ImageDraw.Draw(glow_layer, "RGBA")
    face_draw = ImageDraw.Draw(face_layer, "RGBA")

    _paint_background(background, image.size)

    outer_radius = float(description["guard_vessel_outer_radius"])
    z_min = float(description["z_min"])
    z_max = float(description["z_max"])
    scale = min(image.width / 380.0, image.height / 320.0)
    center = (image.width / 2.0 + 80.0, image.height * 0.76)

    _draw_ground_rings(background, center, scale, outer_radius, z_min)

    faces = _build_scene_faces(description["render_solids"], scale, center)
    faces.sort(key=lambda face: face["depth"])
    for face in faces:
        if face["glow"] is not None:
            glow_draw.polygon(face["points"], fill=face["glow"])
        face_draw.polygon(face["points"], fill=face["fill"], outline=face["outline"])

    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=18))
    image = Image.alpha_composite(image, glow_layer)
    image = Image.alpha_composite(image, face_layer)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output_path)
    return output_path


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
    outer_radius = float(solid["outer_radius"])
    inner_radius = float(solid.get("inner_radius", 0.0))
    z_min = float(solid.get("z_min", 0.0))
    z_max = float(solid.get("z_max", float(solid.get("height", 0.0))))
    if outer_radius <= 0.0 or z_max <= z_min:
        return None

    x_center = float(solid.get("x", 0.0))
    y_center = float(solid.get("y", 0.0))
    theta_values = [2.0 * pi * step / sides for step in range(sides)]

    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    outer_bottom: list[int] = []
    outer_top: list[int] = []

    for theta in theta_values:
        outer_bottom.append(len(vertices))
        vertices.append((x_center + outer_radius * cos(theta), y_center + outer_radius * sin(theta), z_min))
    for theta in theta_values:
        outer_top.append(len(vertices))
        vertices.append((x_center + outer_radius * cos(theta), y_center + outer_radius * sin(theta), z_max))

    inner_bottom: list[int] = []
    inner_top: list[int] = []
    center_bottom = -1
    center_top = -1
    if inner_radius > 0.0:
        for theta in theta_values:
            inner_bottom.append(len(vertices))
            vertices.append((x_center + inner_radius * cos(theta), y_center + inner_radius * sin(theta), z_min))
        for theta in theta_values:
            inner_top.append(len(vertices))
            vertices.append((x_center + inner_radius * cos(theta), y_center + inner_radius * sin(theta), z_max))
    else:
        center_bottom = len(vertices)
        vertices.append((x_center, y_center, z_min))
        center_top = len(vertices)
        vertices.append((x_center, y_center, z_max))

    for step in range(sides):
        next_step = (step + 1) % sides
        faces.append((outer_bottom[step], outer_bottom[next_step], outer_top[step]))
        faces.append((outer_bottom[next_step], outer_top[next_step], outer_top[step]))

        if inner_radius > 0.0:
            faces.append((inner_bottom[next_step], inner_bottom[step], inner_top[step]))
            faces.append((inner_top[next_step], inner_bottom[next_step], inner_top[step]))
            faces.append((outer_bottom[next_step], outer_bottom[step], inner_bottom[step]))
            faces.append((outer_bottom[next_step], inner_bottom[step], inner_bottom[next_step]))
            faces.append((outer_top[step], outer_top[next_step], inner_top[step]))
            faces.append((outer_top[next_step], inner_top[next_step], inner_top[step]))
        else:
            faces.append((outer_bottom[next_step], outer_bottom[step], center_bottom))
            faces.append((outer_top[step], outer_top[next_step], center_top))

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


def _draw_ground_rings(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    scale: float,
    outer_radius: float,
    z_min: float,
) -> None:
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
    ordered_solids = sorted(
        solids,
        key=lambda solid: (float(solid["outer_radius"]), float(solid["z_max"]) - float(solid["z_min"])),
        reverse=True,
    )
    for solid in ordered_solids:
        material = solid.get("material")
        if material is None:
            continue
        style = MATERIAL_STYLES.get(material, MATERIAL_STYLES["pipe"])
        outer_radius = float(solid["outer_radius"])
        inner_radius = float(solid.get("inner_radius", 0.0))
        x_center = float(solid.get("x", 0.0))
        y_center = float(solid.get("y", 0.0))
        z_min = float(solid["z_min"])
        z_max = float(solid["z_max"])
        cutaway = bool(solid.get("cutaway"))
        theta_start = CUTAWAY_START if cutaway else 0.0
        theta_stop = CUTAWAY_STOP if cutaway else 2.0 * pi
        segments = 36 if outer_radius > 5.0 else 18
        theta_values = [theta_start + (theta_stop - theta_start) * index / segments for index in range(segments + 1)]

        outer_top = [
            (x_center + outer_radius * cos(theta), y_center + outer_radius * sin(theta), z_max)
            for theta in theta_values
        ]
        outer_bottom = [
            (x_center + outer_radius * cos(theta), y_center + outer_radius * sin(theta), z_min)
            for theta in theta_values
        ]
        if inner_radius > 0.0:
            inner_top = [
                (x_center + inner_radius * cos(theta), y_center + inner_radius * sin(theta), z_max)
                for theta in theta_values
            ]
            inner_bottom = [
                (x_center + inner_radius * cos(theta), y_center + inner_radius * sin(theta), z_min)
                for theta in theta_values
            ]
        else:
            inner_top = [(x_center, y_center, z_max)] * (segments + 1)
            inner_bottom = [(x_center, y_center, z_min)] * (segments + 1)

        for index in range(segments):
            theta_mid = (theta_values[index] + theta_values[index + 1]) / 2.0
            shade = 0.58 + 0.34 * max(0.0, cos(theta_mid - pi / 3.0))
            faces.append(
                _face_entry(
                    [
                        outer_top[index],
                        outer_top[index + 1],
                        outer_bottom[index + 1],
                        outer_bottom[index],
                    ],
                    scale,
                    center,
                    _shade_color(style["fill"], shade, 235),
                    _shade_color(style["edge"], min(1.0, shade + 0.1), 180),
                    _maybe_glow(style["glow"], material, 60 if material == "fuel_salt" else 0),
                )
            )

            if inner_radius > 0.0:
                inner_shade = 0.48 + 0.22 * max(0.0, cos(theta_mid + pi / 2.6))
                faces.append(
                    _face_entry(
                        [
                            inner_top[index + 1],
                            inner_top[index],
                            inner_bottom[index],
                            inner_bottom[index + 1],
                        ],
                        scale,
                        center,
                        _shade_color(style["fill"], inner_shade, 200),
                        None,
                        None,
                    )
                )

        top_polygon = outer_top + list(reversed(inner_top))
        faces.append(
            _face_entry(
                top_polygon,
                scale,
                center,
                _shade_color(style["fill"], 1.04, 220),
                _shade_color(style["edge"], 1.0, 200),
                _maybe_glow(style["glow"], material, 72 if material == "fuel_salt" else 0),
            )
        )

        if cutaway:
            for theta_value in (theta_start, theta_stop):
                outer_top_point = (x_center + outer_radius * cos(theta_value), y_center + outer_radius * sin(theta_value), z_max)
                outer_bottom_point = (x_center + outer_radius * cos(theta_value), y_center + outer_radius * sin(theta_value), z_min)
                inner_top_point = (
                    x_center + inner_radius * cos(theta_value),
                    y_center + inner_radius * sin(theta_value),
                    z_max,
                )
                inner_bottom_point = (
                    x_center + inner_radius * cos(theta_value),
                    y_center + inner_radius * sin(theta_value),
                    z_min,
                )
                faces.append(
                    _face_entry(
                        [inner_top_point, outer_top_point, outer_bottom_point, inner_bottom_point],
                        scale,
                        center,
                        _shade_color(style["fill"], 0.82, 215),
                        _shade_color(style["edge"], 0.9, 160),
                        None,
                    )
                )
    return faces


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
    if alpha <= 0 or material not in {"fuel_salt", "air"}:
        return None
    red, green, blue = ImageColor.getrgb(hex_color)
    return (red, green, blue, alpha)
