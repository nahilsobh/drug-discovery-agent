# Skill: TargetBiologyScraper

## Description
This skill identifies high-confidence disease associations for any genomic target using the Open Targets Platform. It is the primary discovery tool for finding new therapeutic indications (Indication Expansion) for RedClaw assets.

## Capabilities
- Resolves Ensembl IDs (e.g., ENSG00000129514) to approved gene symbols (e.g., ESR1).
- Retrieves top 10 disease associations based on genetic, somatic, and literature evidence.
- Filters results by a confidence score (default > 0.5) to ensure clinical relevance.

## When to Use
- Use this skill when the user asks about the "potential," "biological evidence," or "future indications" for a drug or gene.
- Use this as the **first step** in a discovery workflow before checking the clinical pipeline (ClinicalTrials.gov).

## Input Schema (JSON)
```json
{
  "ensembl_id": "string (Required: The Ensembl Gene ID)",
  "min_score": "number (Optional: Confidence threshold 0.0-1.0, default 0.5)"
}

