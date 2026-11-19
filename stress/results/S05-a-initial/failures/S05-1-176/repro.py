# S05 case S05-1-176 (tangent_features, input: ambiguous)
# verdict: {'class': 'defect', 'kinds': ['disconnected_solids', 'mounting_surface_reference_unstable']}
# run: uv run python stress/results/S05-a-initial/failures/S05-1-176/repro.py
import math, time
from cad import CadError, Part, Profile, Plane, _occ

print(result)
