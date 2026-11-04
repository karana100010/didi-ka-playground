#!/usr/bin/env bash
# Sets the production site access code and admin sign-in password on Vercel (asked for, never stored).
# Run from the repository root: bash scripts/vercel_secrets.sh, then redeploy (vercel --prod).
set -euo pipefail
cd "$(dirname "$0")/.."

read -rsp 'Password for the access code and admin sign-in: ' PW; echo
[ -n "$PW" ] || { echo 'No password given.'; exit 1; }

for name in PLAYGROUND_ACCESS_CODE PLAYGROUND_ADMIN_PASSWORD; do
  vercel env rm "$name" production -y >/dev/null 2>&1 || true
  printf '%s' "$PW" | vercel env add "$name" production >/dev/null
  echo "set $name"
done
