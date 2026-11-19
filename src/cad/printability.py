"""Print readiness: mass, bed fit, thin walls and overhangs.

This is the fast, always-available pre-slice check.  It answers "is this
printable as modelled, and where will it struggle" without a slicer:

- **mass** from the solid volume and the material's density;
- **bed fit** of the part in its print orientation (``Part.print_on``), plus
  which axis-aligned sizes would fit if the current one does not;
- **thin walls** from ray-cast local thickness (see :mod:`cad.analysis`), with
  the faces that fall below two nozzle widths;
- **overhangs** from face normals against the build direction: any downward
  face whose slope is shallower than the threshold.

Overhangs and thin walls are reported per BRep face, indexed in the same order
as ``_occ.faces(shape)`` — the order the tessellator writes into a mesh's
``face_ids`` — so a viewer can colour exactly those faces.  Everything is
measured on the part as modelled: the build direction and the bed plane are
expressed in the design frame (``Part.print_frame``), so points, normals and
named references all belong to the geometry the viewer draws.

These are geometric estimates, not a slicer's verdict: supports, bridging,
adaptive layers and the extruder's own limits all change the outcome.  Use
``cad slice`` for print time and material from Bambu Studio.
"""

from __future__ import annotations

import math

from . import _occ
from .analysis import thickness_samples
from .errors import CadError

# Build volumes in mm (X, Y, Z).  Any "x,y,z" string or triple is accepted too,
# so a printer that is not listed is still usable.
BEDS: dict[str, tuple[float, float, float]] = {
    "A1": (256.0, 256.0, 256.0),
    "A1 mini": (180.0, 180.0, 180.0),
    "P1P": (256.0, 256.0, 256.0),
    "P1S": (256.0, 256.0, 256.0),
    "X1C": (256.0, 256.0, 256.0),
}
DEFAULT_BED = (256.0, 256.0, 256.0)

# A 0.4 mm nozzle laid down as two perimeters: below this, walls print with gaps
# or as a single bead with poor strength.
WALL_PERIMETERS = 2.0
ROUGH_FLOW_MM3_S = 12.0  # 0.4 mm nozzle, ~0.2 mm layers, PLA; only for a rough time

# Normals are cheap to sample (no ray casts), so curved faces get a fine grid:
# 16 steps over a cylinder's 360 deg is 22.5 deg, fine enough to find the 90 deg
# band that overhangs a 45 deg limit.  Planar faces need one sample.
CURVED_SAMPLES = 16

# Surfaces within this height of the lowest point sit on the plate: the plate
# supports them, so they are not overhangs.  Covers a chamfered or filleted base
# and elephant-foot compensation (0.2-0.5 mm) without hiding real ledges.
BED_BAND = 0.6

# A face exactly at the threshold (a 45 deg chamfer at 45 deg) must not flip on
# floating-point noise.
THRESHOLD_EPS_DEG = 0.05


def parse_bed(value) -> tuple[float, float, float]:
    """``"A1 mini"``, ``(x, y, z)`` or ``"256,256,256"`` -> (x, y, z) in mm."""
    if value is None:
        return DEFAULT_BED
    if isinstance(value, (tuple, list)):
        if len(value) != 3:
            raise CadError("INVALID_ARGUMENT", f"bed needs three sizes (x, y, z), got {value!r}")
        return tuple(float(v) for v in value)
    text = str(value).strip()
    if text in BEDS:
        return BEDS[text]
    for k, v in BEDS.items():
        if k.lower() == text.lower():
            return v
    parts = [p.strip() for p in text.replace("x", ",").replace("*", ",").split(",") if p.strip()]
    if len(parts) == 3:
        try:
            return tuple(float(p) for p in parts)
        except ValueError:
            pass
    raise CadError("INVALID_ARGUMENT", f"cannot read bed {value!r}",
                   hint=f"use one of {', '.join(BEDS)}, or x,y,z in mm (e.g. 256,256,256)")


def material_for(part, name: str | None = None) -> dict:
    """Density for a part's own material, or for ``name``/PLA when it has none."""
    from .materials import resolve

    if name is None and getattr(part, "material", None) is not None:
        m = part.material  # may be a custom Material, not only a built-in
        return {"name": m.name, "density_g_cm3": float(m.density) if m.density else None}
    m = resolve(name or "PLA")
    return {"name": m.name, "density_g_cm3": float(m.density) if m.density else None}


def _uv_samples(face, n_u: int, n_v: int | None = None) -> list[tuple[float, float]]:
    """UV parameter pairs inside the trimmed face (a grid)."""
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.BRepTools import BRepTools
    from OCP.gp import gp_Pnt2d
    from OCP.TopAbs import TopAbs_IN

    n_v = n_v or n_u
    umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)
    out = []
    for i in range(n_u):
        for j in range(n_v):
            u = umin + (i + 0.5) / n_u * (umax - umin)
            v = vmin + (j + 0.5) / n_v * (vmax - vmin)
            if BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), 1e-7).State() == TopAbs_IN:
                out.append((u, v))
    return out


def _samples_with_points(face, up, n_u: int, n_v: int) -> list[tuple[float, float, tuple[float, float, float]]]:
    """(u, v, world point) samples on a face."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    surf = BRepAdaptor_Surface(face)
    out = []
    for u, v in _uv_samples(face, n_u, n_v):
        p = surf.Value(u, v)
        out.append((u, v, (p.X(), p.Y(), p.Z())))
    return out


def _normal_grid(face) -> tuple[int, int]:
    """(u, v) samples needed to see a face's normals: cheap where they are constant.

    A plane has one normal, but its UV middle can fall outside the trimmed face
    (a counterbore floor is an annulus), so probe a small grid and keep the
    samples that land inside.
    """
    kind = _occ.surface_type(face)
    if kind == "plane":
        return 3, 3
    if kind in ("cylinder", "cone"):
        # the normal is constant along the ruling, so only the angle matters;
        # three heights still catch geometry that dips to the bed
        return 64, 3
    return CURVED_SAMPLES, CURVED_SAMPLES


def _overhangs(part, up, h0: float, threshold_deg: float) -> list[dict]:
    """Downward-facing regions shallower than the threshold, per face.

    ``up`` is the build direction in the design frame and ``h0`` the lowest
    point along it (see ``Part.print_frame``); faces in the bed band are not
    overhangs because the plate supports them.
    """
    threshold = min(90.0, max(0.0, float(threshold_deg)))
    limit = -math.cos(math.radians(max(0.3, threshold - THRESHOLD_EPS_DEG)))
    out = []
    for fi, face in enumerate(_occ.faces(part.shape)):
        n_u, n_v = _normal_grid(face)
        samples = _samples_with_points(face, up, n_u, n_v)
        if not samples:
            continue
        bad, angles = 0, []
        for u, v, p in samples:
            n = _occ.face_normal_at(face, u, v)
            nz = n[0] * up[0] + n[1] * up[1] + n[2] * up[2]
            if nz >= limit:
                continue
            h = p[0] * up[0] + p[1] * up[1] + p[2] * up[2] - h0
            if h <= BED_BAND:
                continue
            bad += 1
            angles.append(math.degrees(math.acos(max(-1.0, min(1.0, -nz)))))
        if not bad:
            continue
        area = _occ.area(face) * bad / len(samples)
        if area <= 1e-9:
            continue
        out.append({"face": fi, "area": round(area, 3), "angle_deg": round(min(angles), 1),
                    "refs": part._names_of(face, "face")})
    out.sort(key=lambda d: (d["angle_deg"], -d["area"]))
    return out


def _thin(part, min_wall: float, per_face: int) -> tuple[list[dict], dict]:
    """Faces whose thinnest sample is below ``min_wall``."""
    worst: dict[int, dict] = {}
    for s in thickness_samples(part, per_face, identify=True):
        fi = s.get("face")
        if fi is None:
            continue
        cur = worst.get(fi)
        if cur is None or s["thickness"] < cur["thickness"]:
            worst[fi] = s
    faces = [{"face": fi, "thickness": round(s["thickness"], 3), "at": [round(c, 3) + 0.0 for c in s["point"]],
              "refs": s.get("faces") or []} for fi, s in worst.items() if s["thickness"] < min_wall]
    faces.sort(key=lambda d: d["thickness"])
    if not worst:
        return [], {"min": None, "count": 0, "threshold": round(min_wall, 3)}
    return faces, {"min": round(min(s["thickness"] for s in worst.values()), 3), "count": len(faces),
                   "threshold": round(min_wall, 3)}


def _fits(size, bed) -> bool:
    return all(size[i] <= bed[i] + 1e-9 for i in range(3))


def _orientations(size) -> list[list[float]]:
    """Distinct axis-aligned sizes (all realisable by a 90 degree rotation) apart from the current one."""
    seen, out = set(), []
    for perm in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
        box = [size[i] for i in perm]
        key = tuple(round(v, 6) for v in box)
        if key in seen or key == tuple(round(v, 6) for v in size):
            continue
        seen.add(key)
        out.append(box)
    return out


def report(part, *, material: str | None = None, bed=DEFAULT_BED, overhang_deg: float = 45.0,
           nozzle: float = 0.4, min_wall: float | None = None, per_face: int = 3, revision: int | None = None) -> dict:
    """Print readiness of ``part`` in its print orientation."""
    from .materials import resolve  # noqa: F401  (material_for reports unknown names clearly)

    bed = parse_bed(bed)
    mat = material_for(part, material)
    printed = part.as_printed() if getattr(part, "print_down", None) else part
    lo, hi = _occ.bbox(printed.shape)
    size = [round(b - a, 3) + 0.0 for a, b in zip(lo, hi)]
    volume = round(float(part.volume), 3)
    density = mat.get("density_g_cm3")
    mass = round(volume / 1000.0 * density, 2) if density else None
    wall = float(min_wall) if min_wall is not None else WALL_PERIMETERS * nozzle
    up, h0 = part.print_frame()
    over_faces = _overhangs(part, up, h0, overhang_deg)
    over_area = round(sum(f["area"] for f in over_faces), 3)

    solid = volume > 0 and _occ.solids(part.shape) != []
    if solid:
        thin_faces, thin_summary = _thin(part, wall, per_face)
    else:
        thin_faces, thin_summary = [], {"min": None, "count": 0, "threshold": round(wall, 3)}
        over_faces, over_area = [], 0.0

    fits = _fits(size, bed)
    warnings: list[dict] = []
    if not solid:
        warnings.append({"code": "NOT_A_SOLID", "message": "the part has no enclosed solid (volume 0), so thickness and "
                         "overhang checks were skipped", "hint": "import a solid, or check that the booleans left a body"})
    if not fits:
        overflow = [round(size[i] - bed[i], 2) for i in range(3)]
        alt = [o for o in _orientations(size) if _fits(o, bed)]
        warnings.append({"code": "BED_OVERFLOW", "message": f"{size[0]:g} x {size[1]:g} x {size[2]:g} mm does not fit the "
                         f"{bed[0]:g} x {bed[1]:g} x {bed[2]:g} mm bed", "overflow_mm": overflow,
                         "hint": (f"an axis-aligned size such as {alt[0][0]:g} x {alt[0][1]:g} x {alt[0][2]:g} would fit; "
                                  "set it in the script with part.print_on(...)") if alt else "scale the part down or split it"})
    if thin_faces:
        why = ("two %.2g mm perimeters need %.2g mm" % (nozzle, wall)) if min_wall is None else "below the given minimum"
        warnings.append({"code": "THIN_WALL", "message": f"{len(thin_faces)} face(s) thinner than {wall:g} mm "
                         f"(thinnest {thin_summary['min']:g} mm); {why}",
                         "hint": "thicken the wall, or accept a single-perimeter wall where it is not loaded"})
    if over_faces:
        warnings.append({"code": "OVERHANG", "message": f"{over_area:g} mm2 of downward-facing surface is shallower than "
                         f"{overhang_deg:g} deg; it will need supports or bridging",
                         "hint": "chamfer under 45 deg, lay the part on another face, or print with supports"})
    if getattr(part, "print_down", None) is None:
        warnings.append({"code": "NO_PRINT_ORIENTATION", "message": "no print orientation is set, so the design pose is "
                         "assessed as the print pose", "hint": "choose the face that goes on the bed: part.print_on(face)"})
    return {
        "revision": revision,
        "part": part.name,
        "material": {"name": mat["name"], "density_g_cm3": density},
        "geometry": {"volume_mm3": volume, "bbox_mm": size, "print_down": getattr(part, "print_down", None)},
        "mass_g": mass,
        "bed": {"size_mm": list(bed), "fits": fits},
        "orientation": {"as_modelled": getattr(part, "print_down", None) is None,
                        "ok": fits,
                        "fits_if_rotated": [o for o in _orientations(size) if _fits(o, bed)]},
        "thin": {"threshold_mm": round(wall, 3), "min_mm": thin_summary["min"], "count": len(thin_faces),
                 "samples_per_face": per_face, "faces": thin_faces},
        "overhang": {"threshold_deg": overhang_deg, "area_mm2": over_area, "count": len(over_faces),
                     "convention": "slope from horizontal; a larger threshold counts more of the part as overhang",
                     "faces": over_faces},
        "estimate": {"time_min": round(volume / ROUGH_FLOW_MM3_S / 60.0, 1) if volume else 0.0,
                     "assumed_flow_mm3_s": ROUGH_FLOW_MM3_S,
                     "note": "rough solid-volume estimate at a fixed flow rate; `cad slice` is the real number"},
        "warnings": warnings,
    }
