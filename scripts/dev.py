"""Open the workspace with an editable starter project in the user's project library."""
import argparse
import os
import sys
import tempfile
from pathlib import Path
from shutil import copyfile

try:
    from cad.cli import main
except ImportError as error:
    print(f"Could not load the CAD runtime: {error}", file=sys.stderr)
    if sys.platform.startswith("linux"):
        print("See the Linux system libraries in the README.", file=sys.stderr)
    raise SystemExit(1)


def starter_project(library: Path) -> Path:
    project = library / "three-lobe-spinner"
    if not project.exists():
        library.mkdir(parents=True, exist_ok=True)
        sample = Path(__file__).resolve().parents[1] / "samples/improved-fidget-spinners/trilobe"
        with tempfile.TemporaryDirectory(dir=library, prefix=".starter-") as scratch:
            ready = Path(scratch) / project.name
            ready.mkdir()
            for filename in ("cad.toml", "model.py"):
                copyfile(sample / filename, ready / filename)
            try:
                ready.rename(project)
            except OSError:
                if not project.is_dir():
                    raise
    return project


args = sys.argv[1:]
if sys.platform == "darwin":
    data = Path.home() / "Library/Application Support"
else:
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
library = Path(os.environ.get("CAD_WORKBENCH_PROJECTS") or data / "CAD Workbench/projects").expanduser()
options = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
options.add_argument("--project", "-C")
options.add_argument("--library", type=Path, default=library)
options.add_argument("--help", "-h", action="store_true")
launch, _ = options.parse_known_args(args)
defaults = []
if not launch.help:
    defaults = ["--library", str(launch.library)]
    if launch.project is None:
        defaults += ["--project", str(starter_project(launch.library))]
code, out, err = main(["serve", *defaults, *args])
sys.stdout.write(out)
sys.stderr.write(err)
raise SystemExit(code)
