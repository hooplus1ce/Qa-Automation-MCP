#!/usr/bin/env bash
set -euo pipefail

if [[ "${APS_E2E_DETAIL:-0}" == "1" ]]; then
  if [[ "${APS_DETAIL_RUN:-0}" != "1" ]]; then
    echo "APS detail E2E skipped; set APS_DETAIL_RUN=1 after selecting the detail module."
    exit 0
  fi
  APS_E2E=1 APS_DETAIL_RUN=1 uv run python -m unittest \
    tests.e2e.aps_clean_changeover_spec.ApsMaterialSubstituteDetailE2E -v
else
  APS_E2E=1 uv run python -m unittest \
    tests.e2e.aps_clean_changeover_spec.ApsCleanChangeoverE2E -v
fi
