"""Shared session accumulator — imported by all tool modules and run_agent.py."""

SESSION: dict = {
    "question":              "",
    "gaps":                  [],
    "portfolio":             [],
    "combinations":          [],
    "literature":            [],
    "regulatory":            [],
    "trials":                [],
    "biology":               [],
    "arxiv_papers":          [],
    "trial_outcomes":        [],
    "repurposing":           [],
    "orphan_flags":          [],
    "protein_structures":    [],
    "competitive_signals":   [],
    "fold_results":          [],
    "variant_effects":       [],
    "admet_profiles":        [],
    "mutation_landscapes":   [],
    "phenocopiers":          [],
}
