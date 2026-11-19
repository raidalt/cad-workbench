# S05 case S05-1-116 (holes_near_edges, input: ambiguous)
# verdict: {'class': 'defect', 'kinds': ['disconnected_solids', 'mounting_surface_reference_unstable']}
# run: uv run python stress/results/S05-a-initial/failures/S05-1-116/repro.py
import math, time
from cad import CadError, Part, Profile, Plane, _occ

print(result)
