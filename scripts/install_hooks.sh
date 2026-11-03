#!/bin/bash
# Installs the pre-commit hook that blocks secrets, real identifiers and private terms.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
cat > "$root/.git/hooks/pre-commit" <<'HOOK'
#!/bin/bash
exec python3 -I "$(git rev-parse --show-toplevel)/scripts/scan_sensitive.py" --staged
HOOK
chmod +x "$root/.git/hooks/pre-commit"
echo "pre-commit hook installed"
