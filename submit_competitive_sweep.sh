#!/usr/bin/env bash
# Submit one CEO strategic briefing job per competitor, staggered by 30s
# to avoid port collisions when multiple jobs land on the same node.
#
# Usage:
#   bash submit_competitive_sweep.sh
#
# Output per job:
#   logs/competitive_<slug>_<JOBID>.out   — full console log + audit table
#   reports/Roche_vs_<slug>_<date>.pdf    — PDF briefing
#   logs/audit_<session_id>.jsonl         — structured JSON Lines audit trail

set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT"
mkdir -p logs reports

# Company name → filesystem slug
declare -A SLUGS=(
    ["AstraZeneca"]="astrazeneca"
    ["Eli Lilly"]="eli_lilly"
    ["Novartis"]="novartis"
    ["Pfizer"]="pfizer"
    ["Merck"]="merck"
    ["Bristol-Myers Squibb"]="bms"
    ["AbbVie"]="abbvie"
    ["Johnson & Johnson"]="jnj"
)

SUBMITTED=()
DELAY=0

for COMPETITOR in "AstraZeneca" "Eli Lilly" "Novartis" "Pfizer" \
                  "Merck" "Bristol-Myers Squibb" "AbbVie" "Johnson & Johnson"; do
    SLUG="${SLUGS[$COMPETITOR]}"

    echo "Submitting: Roche vs ${COMPETITOR} (slug=${SLUG}, delay=${DELAY}s)..."

    JOB_ID=$(
        COMPETITOR="$COMPETITOR" \
        COMPANY_SLUG="$SLUG" \
        sbatch \
            --job-name="$SLUG" \
            --begin="now+${DELAY}" \
            "${PROJECT}/competitive_briefing_slurm.sh" \
        | awk '{print $NF}'
    )

    echo "  → Job ${JOB_ID}"
    SUBMITTED+=("${JOB_ID}:${COMPETITOR}")
    DELAY=$(( DELAY + 30 ))
done

echo ""
echo "============================================================"
echo "  SUBMITTED ${#SUBMITTED[@]} jobs:"
for entry in "${SUBMITTED[@]}"; do
    JID="${entry%%:*}"
    NAME="${entry#*:}"
    printf "  %-10s  %s\n" "$JID" "$NAME"
done
echo ""
echo "  Monitor:  squeue -u $USER"
echo "  Watch all:  tail -f logs/competitive_*_*.out"
echo "  Results:  ls -lh reports/Roche_vs_*.pdf"
echo "  Audit:    ls -lh logs/audit_*.jsonl"
echo "============================================================"
