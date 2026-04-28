#!/usr/bin/env bash
# Submit one repurposing analysis job per competitor, staggered 30s apart.
#
# Usage:
#   bash submit_repurposing_sweep.sh
#
# Output per job:
#   logs/repurposing_<slug>_<JOBID>.out   — full log + audit table
#   reports/Repurposing_<slug>_<date>.pdf — ranked repurposing PDF
#   logs/audit_<session_id>.jsonl         — structured audit trail

set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT"
mkdir -p logs reports

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

    echo "Submitting repurposing: ${COMPETITOR} (slug=${SLUG}, delay=${DELAY}s)..."

    JOB_ID=$(
        COMPETITOR="$COMPETITOR" \
        COMPANY_SLUG="$SLUG" \
        sbatch \
            --job-name="$SLUG" \
            --begin="now+${DELAY}" \
            --export=ALL,COMPETITOR="$COMPETITOR",COMPANY_SLUG="$SLUG" \
            "${PROJECT}/repurposing_slurm.sh" \
        | awk '{print $NF}'
    )

    echo "  → Job ${JOB_ID}"
    SUBMITTED+=("${JOB_ID}:${COMPETITOR}")
    DELAY=$(( DELAY + 30 ))
done

echo ""
echo "============================================================"
echo "  SUBMITTED ${#SUBMITTED[@]} repurposing jobs:"
for entry in "${SUBMITTED[@]}"; do
    JID="${entry%%:*}"
    NAME="${entry#*:}"
    printf "  %-10s  %s\n" "$JID" "$NAME"
done
echo ""
echo "  Monitor:  squeue -u $USER"
echo "  Watch:    tail -f logs/repurposing_*_*.out"
echo "  Results:  ls -lh reports/Repurposing_*.pdf"
echo "  Audit:    ls -lh logs/audit_*.jsonl"
echo "============================================================"
