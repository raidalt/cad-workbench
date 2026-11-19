"""Assertions about model geometry that also run during CAD chat preflight."""

from collections.abc import Iterable, Sequence
from math import isfinite

from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.TopAbs import TopAbs_IN, TopAbs_ON, TopAbs_OUT
from OCP.gp import gp_Pnt

from . import _occ
from .errors import INVALID_ARGUMENT, VALIDATION_FAILED, CadError
from .part import Part


def require_points(
    part: Part,
    points: Iterable[Sequence[float]],
    *,
    material: bool,
    label: str,
) -> None:
    """Require actual solid material or void at the given model-space points.

    Points on a boundary fail either check. A few samples can verify a local
    design intention (an open channel or a web) without certifying an entire part.
    """
    solids = _occ.solids(part.shape)
    if not solids:
        raise CadError(VALIDATION_FAILED, f"{part.name}: {label} requires solid geometry")
    classifiers = [BRepClass3d_SolidClassifier(solid) for solid in solids]
    expected = "material" if material else "void"
    for sample in points:
        xyz = tuple(float(value) for value in sample)
        if len(xyz) != 3 or not all(isfinite(value) for value in xyz):
            raise CadError(INVALID_ARGUMENT, "design check points must contain three finite coordinates")
        point = gp_Pnt(*xyz)
        states = []
        for classifier in classifiers:
            classifier.Perform(point, 1e-6)
            states.append(classifier.State())
        passed = TopAbs_IN in states if material else all(state == TopAbs_OUT for state in states)
        if not passed:
            if TopAbs_IN in states:
                observed = "material"
            elif TopAbs_ON in states:
                observed = "boundary"
            elif all(state == TopAbs_OUT for state in states):
                observed = "void"
            else:
                observed = "unclassified"
            raise CadError(
                VALIDATION_FAILED,
                f"{part.name}: {label} expected {expected} at {xyz} mm, found {observed}",
                details={"part": part.name, "check": label, "point": list(xyz), "expected": expected,
                         "observed": observed},
            )


def require_bounds(
    part: Part,
    *,
    minimum: Sequence[float],
    maximum: Sequence[float],
    tolerance: float = 0.05,
) -> None:
    """Check the finished axis-aligned envelope in model coordinates (mm)."""
    bounds = (tuple(map(float, minimum)), tuple(map(float, maximum)))
    if (any(len(point) != 3 or not all(map(isfinite, point)) for point in bounds)
            or not isfinite(tolerance) or tolerance < 0
            or any(lo > hi for lo, hi in zip(*bounds))):
        raise CadError(INVALID_ARGUMENT, "expected ordered finite XYZ bounds and a nonnegative finite tolerance")
    if part.solid_count == 0:
        raise CadError(VALIDATION_FAILED, f"{part.name}: bounding check requires a solid")
    box = part.bbox
    for side, measured, target in zip(("minimum", "maximum"), (box.min, box.max), bounds):
        for axis, actual, expected in zip("XYZ", measured, target):
            if abs(actual - expected) > tolerance:
                raise CadError(
                    VALIDATION_FAILED,
                    f"{part.name}: {side} {axis} is {actual:g} mm, expected {expected:g} ± {tolerance:g} mm",
                    details={"part": part.name, "axis": axis, "side": side, "actual": actual,
                             "expected": expected, "tolerance": tolerance},
                )
