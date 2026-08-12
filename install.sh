#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INSTALL_BIN="/usr/local/bin"
INSTALL_LIB="/usr/local/lib/wpcron"
CAGEFS_CONF="/etc/cagefs/conf.d/wpcron-wrapper.cfg"

log() {
  printf '[ OK ] %s\n' "$*"
}

fail() {
  printf '[FAIL] %s\n' "$*" >&2
  exit 1
}

[[ "$(id -u)" -eq 0 ]] || fail "installer must run as root"

[[ -f "$SCRIPT_DIR/bin/wpcron-wrapper" ]] ||
  fail "missing bin/wpcron-wrapper"

[[ -f "$SCRIPT_DIR/config/wpcron-wrapper.cfg" ]] ||
  fail "missing config/wpcron-wrapper.cfg"

[[ -f "$SCRIPT_DIR/lib/executor.py" ]] ||
  fail "missing lib/executor.py"

[[ -f "$SCRIPT_DIR/lib/models.py" ]] ||
  fail "missing lib/models.py"

[[ -f "$SCRIPT_DIR/lib/repositories.py" ]] ||
  fail "missing lib/repositories.py"

mkdir -p "$INSTALL_LIB"

# Remove generated/cache and backup files from previous installations.
rm -rf "$INSTALL_LIB/__pycache__"
rm -f "$INSTALL_LIB"/*.bak
rm -f "$INSTALL_LIB"/*.phase1.bak
rm -f "$INSTALL_LIB"/*.pyc

# Install the wrapper.
install -m 0755 \
  "$SCRIPT_DIR/bin/wpcron-wrapper" \
  "$INSTALL_BIN/wpcron-wrapper"

# Install Python source files only.
for file in "$SCRIPT_DIR"/lib/*.py; do
  install -m 0644 \
    "$file" \
    "$INSTALL_LIB/$(basename "$file")"
done

# Install version information.
if [[ -f "$SCRIPT_DIR/VERSION" ]]; then
  install -m 0644 \
    "$SCRIPT_DIR/VERSION" \
    "$INSTALL_LIB/VERSION"
fi

# Install CageFS configuration.
install -m 0644 \
  "$SCRIPT_DIR/config/wpcron-wrapper.cfg" \
  "$CAGEFS_CONF"

# Validate Python syntax without creating __pycache__ or .pyc files.
python3 - "$INSTALL_LIB"/*.py <<'PY'
import sys
from pathlib import Path

for filename in sys.argv[1:]:
  source = Path(filename).read_text()
  compile(source, filename, "exec")
PY

# Verify the Phase 2 model/repository fields before refreshing CageFS.
grep -q 'php_executable' "$INSTALL_LIB/models.py" ||
  fail "Phase 2 models.py was not installed"

grep -q 'relative_script_path' "$INSTALL_LIB/models.py" ||
  fail "Phase 2 models.py is missing relative_script_path"

grep -q 'site_root' "$INSTALL_LIB/models.py" ||
  fail "Phase 2 models.py is missing site_root"

grep -q 'php_executable=row' "$INSTALL_LIB/repositories.py" ||
  fail "Phase 2 repositories.py was not installed"

grep -q 'relative_script_path=row' "$INSTALL_LIB/repositories.py" ||
  fail "Phase 2 repositories.py is missing relative_script_path"

grep -q 'site_root=row' "$INSTALL_LIB/repositories.py" ||
  fail "Phase 2 repositories.py is missing site_root"

# Make absolutely sure generated/cache/backup files are absent before
# CageFS copies the installed tree into the skeleton.
rm -rf "$INSTALL_LIB/__pycache__"
rm -f "$INSTALL_LIB"/*.bak
rm -f "$INSTALL_LIB"/*.phase1.bak
rm -f "$INSTALL_LIB"/*.pyc

command -v cagefsctl >/dev/null 2>&1 ||
  fail "cagefsctl not found"

cagefsctl --force-update

# Final production-tree checks.
[[ -x "$INSTALL_BIN/wpcron-wrapper" ]] ||
  fail "wrapper installation failed"

[[ -f "$INSTALL_LIB/executor.py" ]] ||
  fail "executor installation failed"

[[ -f "$INSTALL_LIB/models.py" ]] ||
  fail "models.py installation failed"

[[ -f "$INSTALL_LIB/repositories.py" ]] ||
  fail "repositories.py installation failed"

if [[ -d "$INSTALL_LIB/__pycache__" ]]; then
  fail "unexpected __pycache__ remains in installed tree"
fi

if compgen -G "$INSTALL_LIB/*.bak" >/dev/null ||
   compgen -G "$INSTALL_LIB/*.phase1.bak" >/dev/null ||
   compgen -G "$INSTALL_LIB/*.pyc" >/dev/null; then
  fail "unexpected backup/bytecode files remain in installed tree"
fi

log "Installed wrapper: $INSTALL_BIN/wpcron-wrapper"
log "Installed Python modules: $INSTALL_LIB"
log "Installed CageFS config: $CAGEFS_CONF"
log "Python syntax validation passed"
log "Phase 2 model/repository validation passed"
log "CageFS template updated"
log "Production tree cleanup passed"
log "Phase 2 executor deployment verified"

echo
echo "Existing cPanel cron entries were NOT changed."
echo "No scheduler database changes were made."
