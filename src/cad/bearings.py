"""Matched radial profiles for a print-in-place plain bearing."""

from math import isfinite, sqrt

from .errors import INVALID_ARGUMENT, CadError
from .profile import Profile


def captive_race_profiles(radius: float, height: float, *, clearance: float = 0.35,
                          ridge: float = 1.6) -> tuple[Profile, Profile]:
    """Return (spindle, bore cutter) profiles to revolve on XZ about world Z.

    Dimensions are millimetres. The spindle has a circumferential ridge between
    two 45-degree ramps. Clearance is the minimum normal gap; the larger radial
    offset preserves that gap on both ramps. Both profiles run from z=0 to height.
    Revolve the cutter through an outer blank of the same height, leaving material
    around the full groove. Keep any grip features outside the bearing interface.
    """
    radius, height, clearance, ridge = map(float, (radius, height, clearance, ridge))
    if not all(isfinite(v) and v > 0 for v in (radius, height, clearance, ridge)):
        raise CadError(INVALID_ARGUMENT, "race dimensions must be finite and positive")
    offset = clearance * sqrt(2)
    if ridge <= offset:
        raise CadError(INVALID_ARGUMENT, "ridge must exceed clearance × sqrt(2) to retain the ring")
    if height <= 2 * ridge:
        raise CadError(INVALID_ARGUMENT, "height must exceed twice the ridge for two 45-degree ramps")

    land = (height - 2 * ridge) / 3
    relief = min(0.4, land / 2, radius / 4)

    def profile(r: float, edge_relief: float) -> Profile:
        return Profile.polygon([
            (0, 0), (r + edge_relief, 0), (r, relief), (r, land),
            (r + ridge, land + ridge), (r + ridge, height - land - ridge),
            (r, height - land), (r, height - relief), (r + edge_relief, height),
            (0, height),
        ])

    return profile(radius, -relief), profile(radius + offset, relief)
