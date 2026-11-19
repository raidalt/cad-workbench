from cad import Part, Profile


def build():
    p = Part("reducer")
    bottom = Profile.circle(32) - Profile.circle(28)
    top = Profile.circle(18, center=(8, 0)) - Profile.circle(14, center=(8, 0))
    p.loft([(bottom, 0), (top, 30)], ruled=True, name="transition")
    return p
