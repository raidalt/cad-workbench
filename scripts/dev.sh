#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
root=$(pwd -P)
runtime="$root/.local-cad"
uv="$runtime/tools/uv"

if [ ! -x "$uv" ]; then
  if command -v uv >/dev/null 2>&1; then
    uv=$(command -v uv)
  else
    echo "Setting up the CAD runtime for this checkout..."
    mkdir -p "$runtime/tools"
    installer=$(mktemp "$runtime/uv-installer.XXXXXX")
    trap 'rm -f "$installer"' EXIT
    curl --fail --silent --show-error --location --retry 3 \
      https://astral.sh/uv/0.13.9/install.sh --output "$installer"
    UV_UNMANAGED_INSTALL="$runtime/tools" sh "$installer"
    rm -f "$installer"
    trap - EXIT
  fi
fi

export UV_PROJECT_ENVIRONMENT="$runtime/venv"
echo "Preparing CAD Workbench..."
"$uv" sync --frozen --no-dev --no-install-project --python 3.12
export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1
exec "$UV_PROJECT_ENVIRONMENT/bin/python" "$root/scripts/dev.py" "$@"
