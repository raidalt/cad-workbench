from cad import Part, Profile, captive_race_profiles


def build():
    hub, rotor = Part("hub"), Part("rotor")
    spindle, bore = captive_race_profiles(6, 6, clearance=.42, ridge=1.3)
    hub.revolve(spindle)
    rotor.revolve(Profile.polygon([(0, 0), (17.5, 0), (18, .5),
                                   (18, 5.5), (17.5, 6), (0, 6)]))
    rotor.revolve(bore, op="cut")
    rotor.holes(3, [(13, 0), (0, 13), (-13, 0), (0, -13)])
    return [hub, rotor]
