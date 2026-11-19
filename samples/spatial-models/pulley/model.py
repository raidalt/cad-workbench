from cad import Part, Profile


def build():
    spool = Part("cable_spool")

    # Radial section: (radius, z). The inner radius is 3 mm.
    section = Profile.polygon([
        (3, 0),
        (14, 0),
        (14, 2),
        (8, 5),
        (8, 15),
        (14, 18),
        (14, 20),
        (3, 20),
    ])

    spool.revolve(section, plane="XZ", axis="v", name="turned_spool")
    spool.assert_valid()
    return spool
