from cad import Part, Profile


def build():
    p = Part("spool")
    outline = [(3, 0), (14, 0), (14, 2), (8, 5), (8, 15), (14, 18), (14, 20), (3, 20)]
    p.revolve(Profile.polygon(outline), name="body")
    return p
