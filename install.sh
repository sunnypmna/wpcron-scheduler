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

mkdir -p "$INSTALL_LIB"

install -m 0755 "$SCRIPT_DIR/bin/wpcron-wrapper" \
  "$INSTALL_BIN/wpcron-wrapper"

for file in "$SCRIPT_DIR"/lib/*.py; do
  install -m 0644 "$file" "$INSTALL_LIB/$(basename "$file")"
done

install -m 0644 "$SCRIPT_DIR/config/wpcron-wrapper.cfg" \
  "$CAGEFS_CONF"

if [[ -f "$SCRIPT_DIR/VERSION" ]]; then
  install -m 0644 "$SCRIPT_DIR/VERSION" \
    "$INSTALL_LIB/VERSION"
fi

python3 -m py_compile "$INSTALL_LIB"/*.py

if command -v cagefsctl >/dev/null 2>&1; then
  cagefsctl --force-update
else
  fail "cagefsctl not found"
fi

[[ -x "$INSTALL_BIN/wpcron-wrapper" ]] ||
  fail "wrapper installation failed"

[[ -f "$INSTALL_LIB/executor.py" ]] ||
  fail "executor installation failed"

[[ -f "$INSTALL_LIB/models.py" ]] ||
  fail "models.py installation failed"

[[ -f "$INSTALL_LIB/repositories.py" ]] ||
  fail "repositories.py installation failed"

grep -q 'php_executable' "$INSTALL_LIB/models.py" ||
  fail "Phase 2 models.py was not installed"

grep -q 'php_executable=row' "$INSTALL_LIB/repositories.py" ||
  fail "Phase 2 repositories.py was not installed"

log "Installed wrapper: $INSTALL_BIN/wpcron-wrapper"
log "Installed Python modules: $INSTALL_LIB"
log "Installed CageFS config: $CAGEFS_CONF"
log "Python syntax validation passed"
log "CageFS template updated"
log "Phase 2 executor deployment verified"

echo
echo "Existing cPanel cron entries were NOT changed."
echo "No scheduler database changes were made."
