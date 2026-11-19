from cad import Part, Path, Profile


def build():
    p = Part("tube")
    route = Path.polyline([(0, 0, 0), (0, 0, 28), (32, 0, 28)], radius=12)
    p.sweep(Profile.circle(8) - Profile.circle(5), route, name="bend")
    return p
