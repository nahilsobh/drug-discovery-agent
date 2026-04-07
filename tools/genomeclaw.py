import os
import json
import time
import requests
import concurrent.futures
from tools.session import SESSION
from tools.constants import CLAWAPI_URL, GENOMECLAW_DIR, UNIPROT_URL


def _check_genomeclaw_health() -> bool:
    """Return True if clawapi is reachable; print a warning if not."""
    try:
        r = requests.get(f"{CLAWAPI_URL}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False

# Boltz-1 maximum sequence length (residues)
BOLTZ_MAX_SEQ = 2048

# Known functional domains for proteins that exceed BOLTZ_MAX_SEQ.
# Used by fold_target() as an automatic fallback when the full sequence is too long.
# Format: gene_upper → (start_1based, end_1based_inclusive, domain_name)
#
# GPU constraint note: AMD Radeon R9 M370X + Metal (macOS) limits dispatch_workgroups_x
# to 65535. Boltz-1 PairFormer dispatches (2×seq_len², 1, 1) at its largest kernel,
# so the safe limit is seq_len ≤ 181 (2×181² = 65522 ≤ 65535).
# On CUDA hardware the practical limit is the Boltz-1 model max (2048 residues).
# All entries below are ≤181 aa so they fold on both GPU types.
# Verified: 181-residue fold of LRRK2 kinase domain completes in ~21 min on CPU.
KNOWN_DOMAINS: dict = {
    # LRRK2: kinase catalytic core centered on G2019S (activation loop)
    "LRRK2":   (1929, 2109, "kinase catalytic core (G2019S at 2019)"),   # 181 aa
    # BRCA2: OB3 domain hotspot (frameshift/nonsense variants cluster 2800-2970)
    "BRCA2":   (2800, 2980, "OB3 DNA-binding hotspot"),                   # 181 aa
    # ATM: C-terminal kinase activation loop; D2870 catalytic Asp
    "ATM":     (2876, 3056, "kinase catalytic loop"),                     # 181 aa
    # RYR1: N-terminal hot-spot 1; MH mutations R163C, G248R
    "RYR1":    (83,   263,  "N-terminal hot-spot 1 (MH mutations)"),      # 181 aa
    # NF1: Ras-GAP catalytic core; R1276, R1391 arginine finger mutations
    "NF1":     (1263, 1443, "GRD Ras-GAP catalytic core"),               # 181 aa
    # TTN: Z-disc proximal Ig1-2 repeats; DCM frameshift hotspot
    "TTN":     (1,    181,  "Z-disc Ig1-2 repeats"),                      # 181 aa
    # DNAPKCS: kinase activation loop + FATC domain; S2056/T2609 cluster
    "DNAPKCS": (3948, 4128, "kinase activation loop + FATC"),             # 181 aa
    # MUC16: CA-125 epitope core (used as ovarian cancer biomarker)
    "MUC16":   (14327, 14507, "CA-125 epitope core"),                     # 181 aa
}


def _fetch_uniprot_sequence(gene_symbol: str) -> str:
    """Return the canonical human protein sequence for a gene symbol, or '' on failure."""
    try:
        r = requests.get(
            UNIPROT_URL,
            params={"query": f"gene_exact:{gene_symbol} AND organism_id:9606 AND reviewed:true",
                    "fields": "sequence", "format": "json", "size": 1},
            timeout=10,
        )
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results:
                return results[0].get("sequence", {}).get("value", "")
    except Exception:
        pass
    return ""


def fold_target(gene_symbol_or_sequence: str) -> dict:
    """
    Predict the 3D structure of a protein using GenomeClaw Boltz-1.
    Accepts a gene symbol (looks up canonical sequence) or a raw amino-acid sequence.
    Returns pLDDT confidence, PAE, and a druggability interpretation.
    API: POST http://127.0.0.1:8083/api/fold  (async — submits then polls)
    """
    # Resolve sequence
    seq = gene_symbol_or_sequence.strip()
    is_gene = not all(c in "ACDEFGHIKLMNPQRSTVWYacdefghiklmnpqrstvwy" for c in seq)
    gene_label = seq if is_gene else f"seq[{len(seq)}aa]"
    if is_gene:
        seq = _fetch_uniprot_sequence(seq)
        if not seq:
            return {"status": "error", "note": f"Could not resolve UniProt sequence for {gene_label}"}

    # Domain fallback: if sequence exceeds Boltz-1 limit, trim to known functional domain
    domain_note = None
    if len(seq) > BOLTZ_MAX_SEQ:
        domain_info = KNOWN_DOMAINS.get(gene_label.upper()) if is_gene else None
        if domain_info:
            d_start, d_end, d_name = domain_info
            seq = seq[d_start - 1 : d_end]          # slice is 0-based, coords are 1-based inclusive
            domain_note = f"{d_name} (residues {d_start}–{d_end})"
            gene_label = f"{gene_label}[{d_name}]"
        else:
            return {
                "status": "error",
                "code":   422,
                "note":   (
                    f"Sequence too long ({len(seq)} > {BOLTZ_MAX_SEQ} residues). "
                    f"Add '{gene_label}' to KNOWN_DOMAINS or pass a domain subsequence directly."
                ),
            }

    # Submit fold job
    try:
        r = requests.post(
            f"{CLAWAPI_URL}/api/fold",
            json={"sequence": seq, "sampler": "edm", "diffusion_steps": 50, "num_recycles": 1},
            timeout=15,
        )
    except requests.exceptions.ConnectionError:
        return {"status": "genomeclaw_offline", "note": "GenomeClaw API not reachable at 127.0.0.1:8083"}

    if r.status_code not in (200, 202):
        return {"status": "error", "code": r.status_code, "body": r.text[:200]}

    job_id = r.json().get("job_id") or r.json().get("id")
    if not job_id:
        return {"status": "error", "note": "No job_id in fold response"}

    # Poll for completion — domain folds on CPU can take 10-15 min for ~180 aa
    fold_timeout = 900 if domain_note else 120
    deadline = time.time() + fold_timeout
    while time.time() < deadline:
        time.sleep(5)
        poll = requests.get(f"{CLAWAPI_URL}/api/fold/{job_id}", timeout=10)
        # Use strict=False because PDB content contains embedded newlines
        data = json.loads(poll.text, strict=False)
        if data.get("status") == "completed":
            # API fields: mean_plddt, pdb (not plddt_mean / pdb_str)
            plddt   = data.get("mean_plddt") or data.get("plddt_mean") or 0.0
            elapsed = data.get("elapsed_secs") or data.get("elapsed_ms", 0)
            pdb_preview = (data.get("pdb") or data.get("pdb_str") or "")[:200]

            # pLDDT = 0.0 means confidence scoring not yet available in this model build
            if plddt > 0.70:
                confidence_label = "Confident structure / likely druggable"
            elif plddt > 0.50:
                confidence_label = "Moderate confidence / partially disordered"
            elif plddt > 0.0:
                confidence_label = "Disordered / difficult target"
            else:
                confidence_label = "Structure predicted (pLDDT not available)"

            result = {
                "gene":             gene_label,
                "residues":         len(seq),
                "plddt_mean":       round(plddt, 3),
                "elapsed_secs":     elapsed,
                "confidence_label": confidence_label,
                "pdb_preview":      pdb_preview,
            }
            if domain_note:
                result["domain_used"] = domain_note
            SESSION["fold_results"].append(result)
            return result
        elif data.get("status") == "failed":
            return {"status": "failed", "gene": gene_label, "error": data.get("error", "")}

    return {"status": "timeout", "gene": gene_label, "note": f"Fold exceeded {fold_timeout}s — try a shorter sequence or fewer diffusion steps"}


def score_variant_effect(gene_symbol: str, variant_notation: str) -> dict:
    """
    Score the functional effect of a protein variant using GenomeClaw ESM-2.
    variant_notation: e.g. 'G12C', 'L858R', 'G2019S'
    Returns delta_log_likelihood and an interpretation (resistance risk / tolerated).
    API: POST http://127.0.0.1:8083/api/variant
    """
    import re
    m = re.match(r"^([A-Z])(\d+)([A-Z])$", variant_notation.strip().upper())
    if not m:
        return {"status": "error", "note": f"Cannot parse variant notation '{variant_notation}'. Use format: G12C"}
    wt_aa, position, mut_aa = m.group(1), int(m.group(2)), m.group(3)

    full_seq = _fetch_uniprot_sequence(gene_symbol)
    if not full_seq:
        return {"status": "error", "note": f"Could not resolve sequence for {gene_symbol}"}

    if position < 1 or position > len(full_seq):
        return {"status": "error", "note": f"Position {position} out of range for {gene_symbol} ({len(full_seq)} residues)"}

    # For large proteins (>500 res), extract a ±150 residue window around the mutation.
    # ESM-2 masked marginal scoring uses local context; the window captures the domain.
    MAX_SEQ = 500
    WINDOW = 150
    windowed = False
    seq = full_seq
    seq_position = position  # position relative to seq (may be adjusted after windowing)
    if len(full_seq) > MAX_SEQ:
        start = max(0, position - 1 - WINDOW)
        end   = min(len(full_seq), position - 1 + WINDOW + 1)
        seq = full_seq[start:end]
        seq_position = position - start  # 1-based position within the window
        windowed = True

    try:
        # /api/score uses masked marginal scoring → delta_log_likelihood
        # /api/variant uses embedding distance → cosine_similarity (different metric)
        # Timeout scales with sequence length: ~0.1s/residue for CPU streaming ESM-2
        esm_timeout = max(60, len(seq) // 5)
        r = requests.post(
            f"{CLAWAPI_URL}/api/score",
            json={"sequence": seq, "position": seq_position, "alt": mut_aa},
            timeout=esm_timeout,
        )
    except requests.exceptions.ConnectionError:
        return {"status": "genomeclaw_offline", "note": "GenomeClaw API not reachable at 127.0.0.1:8083"}

    if r.status_code != 200:
        return {"status": "error", "code": r.status_code, "body": r.text[:200]}

    data = r.json()
    # delta_log_likelihood can be None/null (NaN serialized as null in Rust serde_json).
    # This consistently occurs on AMD GPU + Metal backend (macOS) — all ESM-2 embeddings
    # are NaN due to a WGSL shader compatibility issue with this GPU/driver combination.
    # The tool is functional on CUDA (Linux) hardware.
    delta_raw = data.get("delta_log_likelihood")
    if delta_raw is None or (isinstance(delta_raw, float) and delta_raw != delta_raw):
        return {
            "status": "gpu_incompatible",
            "gene": gene_symbol,
            "variant": variant_notation.upper(),
            "note": (
                "ESM-2 GPU forward pass returned NaN — WGSL shaders are incompatible with "
                "AMD Radeon R9 M370X + Metal (macOS). Variant scoring requires CUDA hardware. "
                "Use query_genomeclaw_databases to retrieve ClinVar pathogenicity evidence "
                "for this variant from public databases instead."
            ),
        }
    delta = float(delta_raw)
    assessment = data.get("assessment", "")

    if delta < -2.0:
        interpretation = "Likely damaging — high resistance risk"
    elif delta < -0.5:
        interpretation = "Moderate effect — monitor for resistance"
    else:
        interpretation = "Tolerated — low resistance risk"

    result = {
        "gene":                  gene_symbol,
        "variant":               variant_notation.upper(),
        "wt_aa":                 wt_aa,
        "position":              position,
        "mutant_aa":             mut_aa,
        "delta_log_likelihood":  round(delta, 4),
        "api_assessment":        assessment,
        "interpretation":        interpretation,
        "wt_probability":        data.get("wildtype_probability"),
        "mut_probability":       data.get("mutant_probability"),
        "full_protein_length":   len(full_seq),
        "scored_window":         f"{position - seq_position + 1}-{position - seq_position + len(seq)}" if windowed else "full",
    }
    SESSION["variant_effects"].append(result)
    return result


def predict_admet(smiles_or_drug_name: str) -> dict:
    """
    Predict ADMET (absorption, distribution, metabolism, excretion, toxicity) properties.
    Accepts a drug name (auto-resolves SMILES from PubChem) or a SMILES string directly.
    Uses the clawadmet CLI binary from the genomeclaw workspace.
    Returns TIER-1 (all clear) / TIER-2 (minor flags) / TIER-3 (red flags).
    """
    import subprocess, shutil

    smiles = smiles_or_drug_name.strip()
    drug_label = smiles

    # Resolve SMILES from PubChem if input looks like a drug name (not SMILES)
    if not any(c in smiles for c in "()=[]#@"):
        drug_label = smiles
        try:
            pub_r = requests.get(
                f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{requests.utils.quote(smiles)}/property/IsomericSMILES/JSON",
                timeout=10,
            )
            if pub_r.status_code == 200:
                props = pub_r.json().get("PropertyTable", {}).get("Properties", [])
                if props:
                    smiles = props[0].get("IsomericSMILES") or props[0].get("SMILES") or smiles
        except Exception:
            pass

    # If SMILES still looks like a drug name (no SMILES characters), try one more lookup
    if not any(c in smiles for c in "()=[]#@\\/"):
        return {
            "status": "smiles_not_resolved",
            "drug": drug_label,
            "note": f"Could not resolve SMILES for '{drug_label}' from PubChem. Provide SMILES directly.",
        }

    # Locate clawadmet binary
    binary = os.path.join(GENOMECLAW_DIR, "target", "release", "clawadmet")
    if not os.path.isfile(binary):
        return {
            "status": "genomeclaw_not_built",
            "drug": drug_label,
            "note": "Run: cd genomeclaw && cargo build --release -p genomeclaw-cli",
        }

    try:
        proc = subprocess.run(
            [binary, "predict", "--smiles", smiles, "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return {"status": "error", "drug": drug_label, "stderr": proc.stderr[:300]}

        output = json.loads(proc.stdout)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "drug": drug_label}
    except json.JSONDecodeError:
        return {"status": "error", "drug": drug_label, "note": "Could not parse clawadmet JSON output"}
    except Exception as e:
        return {"status": "error", "drug": drug_label, "note": str(e)}

    # Map results list → dict keyed by model name
    model_map = {r["model"].lower(): r for r in output.get("results", [])}
    summary_score = output.get("summary_score", 1.0)
    summary_label = output.get("summary_label", "UNKNOWN")

    def _label(model_key):
        return model_map.get(model_key, {}).get("label", "Unknown")

    def _val(model_key):
        return model_map.get(model_key, {}).get("value")

    # Tier assignment based on clawadmet labels
    # Note: label values are "hERG blocker" (bad) vs "hERG non-blocker" (good)
    red_flags = []
    herg_label = _label("herg").lower()
    if "blocker" in herg_label and "non-blocker" not in herg_label:
        red_flags.append("hERG blocker (cardiac QTc risk)")
    ames_label = _label("ames").lower()
    if "positive" in ames_label and "non-mutagenic" not in ames_label and "negative" not in ames_label:
        red_flags.append("Ames positive (mutagenic)")
    if summary_label == "RISKY":
        red_flags.append(f"Low summary score ({summary_score:.2f})")

    minor_flags = []
    herg_val = _val("herg") or 0
    if 0.3 < herg_val <= 0.5:
        minor_flags.append("Moderate hERG signal")
    bbb_label = _label("bbb")
    if "non-penetrant" in bbb_label.lower() and any(w in drug_label.lower() for w in ["neuro", "brain", "cns", "alzheimer", "parkinson"]):
        minor_flags.append("Low BBB penetration (consider for CNS targets)")
    solubility_label = _label("solubility")
    if "poorly" in solubility_label.lower():
        minor_flags.append("Poor aqueous solubility")

    if red_flags:
        tier = "TIER-3"
    elif minor_flags:
        tier = "TIER-2"
    else:
        tier = "TIER-1"

    result = {
        "drug":           drug_label,
        "smiles_used":    smiles[:80],
        "tier":           tier,
        "summary_score":  round(summary_score, 3),
        "summary_label":  summary_label,
        "hERG":           _label("herg"),
        "BBB":            _label("bbb"),
        "Ames_mutagenicity": _label("ames"),
        "Solubility":     _label("solubility"),
        "HalfLife":       _label("halflife"),
        "CYP3A4":         _label("cyp3a4"),
        "CYP2D6":         _label("cyp2d6"),
        "Pgp_efflux":     _label("pgp"),
        "PPB":            _label("ppb"),
        "red_flags":      red_flags,
        "minor_flags":    minor_flags,
        "all_predictions": [{"model": r["model"], "label": r["label"], "confidence": r["confidence"]}
                            for r in output.get("results", [])],
    }
    SESSION["admet_profiles"].append(result)
    return result


def query_genomeclaw_databases(gene_or_drug: str, databases: list = None) -> dict:
    """
    Query multiple biomedical databases for a gene symbol or drug name.
    Supported: gnomad (pLI/LoF intolerance), chembl (bioactivity), clinvar (pathogenic variants),
               string (protein interactions), cbioportal (cancer alteration frequency),
               opentargets (disease associations).
    Returns per-DB results and a composite target-richness score.
    """
    from tools.constants import OT_URL

    dbs = [d.lower() for d in databases] if databases else ["gnomad", "chembl", "clinvar", "string", "cbioportal", "opentargets"]
    db_results = {}

    def _query_gnomad(target):
        """gnomAD v4 constraint metrics via GraphQL."""
        q = """{ gene(gene_symbol: "%s", reference_genome: GRCh38) {
                   gnomad_constraint { pLI exp_lof obs_lof lof_z }
                   symbol chrom start stop } }""" % target
        r = requests.post("https://gnomad.broadinstitute.org/api", json={"query": q}, timeout=12)
        gene = r.json().get("data", {}).get("gene") or {}
        c = gene.get("gnomad_constraint") or {}
        if not c:
            return {"status": "no_data", "note": "Gene not found in gnomAD or no constraint data"}
        pli = c.get("pLI")
        return {
            "pLI":     round(pli, 3) if pli is not None else None,
            "obs_lof": c.get("obs_lof"),
            "exp_lof": c.get("exp_lof"),
            "lof_z":   round(c.get("lof_z") or 0, 3),
            "lof_intolerant": pli > 0.9 if pli else None,
            "interpretation": "High LoF intolerance — loss-of-function likely pathogenic" if pli and pli > 0.9
                else ("Moderate LoF constraint" if pli and pli > 0.5 else "LoF tolerant — unlikely essential gene"),
        }

    def _query_chembl(target):
        """ChEMBL REST API — compound bioactivity for gene target."""
        # First resolve target to ChEMBL ID
        url = f"https://www.ebi.ac.uk/chembl/api/data/target/search?q={target}&format=json&limit=1"
        r = requests.get(url, timeout=10)
        targets_data = r.json().get("targets", [])
        if not targets_data:
            return {"status": "no_target", "note": f"{target} not found in ChEMBL target list"}
        chembl_id = targets_data[0]["target_chembl_id"]
        target_name = targets_data[0].get("pref_name", target)

        # Get bioactivities
        act_url = f"https://www.ebi.ac.uk/chembl/api/data/activity?target_chembl_id={chembl_id}&standard_type=IC50&format=json&limit=10&order_by=standard_value"
        act_r = requests.get(act_url, timeout=10)
        activities = act_r.json().get("activities", [])
        best_ic50 = None
        if activities:
            vals = [a.get("standard_value") for a in activities if a.get("standard_value")]
            if vals:
                best_ic50 = float(min(vals))

        # Count total assays
        count_url = f"https://www.ebi.ac.uk/chembl/api/data/activity?target_chembl_id={chembl_id}&format=json&limit=1"
        count_r = requests.get(count_url, timeout=10)
        total = count_r.json().get("page_meta", {}).get("total_count", 0)

        return {
            "chembl_id":     chembl_id,
            "target_name":   target_name,
            "total_assays":  total,
            "best_IC50_nM":  round(best_ic50, 2) if best_ic50 else None,
            "best_pIC50":    round(9 - (best_ic50 / 1e9 if best_ic50 else 0), 2) if best_ic50 else None,
            "druggability_comment": "Well-validated target" if total > 100 else ("Some chemical matter" if total > 10 else "Limited bioactivity data"),
        }

    def _query_clinvar(target):
        """NCBI ClinVar E-utilities — pathogenic variants for a gene."""
        search_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=clinvar&term={target}[gene]+AND+pathogenic[clinical_significance]&retmode=json&retmax=1"
        r = requests.get(search_url, timeout=10)
        data = r.json().get("esearchresult", {})
        count = int(data.get("count", 0))
        return {
            "pathogenic_variants": count,
            "interpretation": f"{count} pathogenic/likely-pathogenic variants — {'high' if count > 50 else 'moderate' if count > 10 else 'low'} disease-gene burden",
        }

    def _query_string(target):
        """STRING-DB v12 — protein interaction partners."""
        url = f"https://string-db.org/api/json/network?identifiers={target}&species=9606&limit=5&caller_identity=roche_ai_factory"
        r = requests.get(url, timeout=12)
        interactions = r.json() if r.status_code == 200 else []
        if not interactions:
            return {"status": "no_interactions", "note": "No STRING interactions found"}
        partners = []
        seen = set()
        for item in interactions[:10]:
            for field in ["preferredName_A", "preferredName_B"]:
                name = item.get(field, "")
                if name and name.upper() != target.upper() and name not in seen:
                    partners.append({"protein": name, "score": item.get("score", 0)})
                    seen.add(name)
        partners.sort(key=lambda x: -x["score"])
        return {
            "top_interactors": partners[:5],
            "interaction_count": len(interactions),
            "note": "High-confidence STRING v12 interactions (score ≥400)",
        }

    def _query_cbioportal(target):
        """cBioPortal REST API — mutation/CNA frequency across TCGA studies."""
        url = f"https://www.cbioportal.org/api/genes/{target}?projection=SUMMARY"
        r = requests.get(url, timeout=12)
        if r.status_code != 200:
            return {"status": "not_found", "note": f"{target} not in cBioPortal gene list"}
        gene_info = r.json()
        entrez_id = gene_info.get("entrezGeneId")

        # Get mutation counts across all studies
        mut_url = f"https://www.cbioportal.org/api/genes/{target}/mutations?projection=SUMMARY&pageSize=1"
        mut_r = requests.get(mut_url, timeout=12)
        # Use cancer type hotspot as proxy
        return {
            "hugo_symbol":   gene_info.get("hugoGeneSymbol", target),
            "entrez_id":     entrez_id,
            "gene_type":     gene_info.get("type", "Unknown"),
            "note":          "cBioPortal gene confirmed. Run monitor_competitive_signals for alteration frequency by cancer type.",
        }

    def _query_opentargets(target):
        """Open Targets — disease associations (already used by get_biology, here for cross-reference)."""
        q = """query($s:String!){search(queryString:$s,entityNames:["target"],page:{index:0,size:1}){
                 hits{id score object{... on Target{id approvedSymbol associatedDiseases(page:{index:0,size:5}){
                   rows{score disease{name}}}}}}}}"""
        r = requests.post(OT_URL, json={"query": q, "variables": {"s": target}}, timeout=10)
        hits = r.json().get("data", {}).get("search", {}).get("hits", [])
        if not hits:
            return {"status": "not_found"}
        obj = hits[0].get("object", {})
        rows = obj.get("associatedDiseases", {}).get("rows", [])
        return {
            "ensembl_id":     obj.get("id"),
            "top_diseases":   [{"disease": row["disease"]["name"], "score": round(row["score"], 3)} for row in rows[:5]],
            "max_score":      round(rows[0]["score"], 3) if rows else 0,
        }

    # Execute queries in parallel
    query_map = {
        "gnomad":       _query_gnomad,
        "chembl":       _query_chembl,
        "clinvar":      _query_clinvar,
        "string":       _query_string,
        "cbioportal":   _query_cbioportal,
        "opentargets":  _query_opentargets,
    }

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(query_map[db], gene_or_drug): db for db in dbs if db in query_map}
        for fut in concurrent.futures.as_completed(futures):
            db = futures[fut]
            try:
                db_results[db] = fut.result()
            except Exception as e:
                db_results[db] = {"status": "error", "note": str(e)[:150]}

    evidence_sources = sum(1 for v in db_results.values()
                           if isinstance(v, dict) and v.get("status") not in ("error", "not_found", "no_data", "no_target", "no_interactions"))

    # Derive summary
    summary = {}
    if "gnomad" in db_results:
        summary["pLI"] = db_results["gnomad"].get("pLI")
        summary["lof_intolerant"] = db_results["gnomad"].get("lof_intolerant")
    if "chembl" in db_results:
        summary["chembl_assays"]   = db_results["chembl"].get("total_assays")
        summary["chembl_best_IC50_nM"] = db_results["chembl"].get("best_IC50_nM")
    if "clinvar" in db_results:
        summary["pathogenic_variants"] = db_results["clinvar"].get("pathogenic_variants")
    if "string" in db_results:
        summary["top_interactors"] = [i["protein"] for i in db_results["string"].get("top_interactors", [])]
    if "opentargets" in db_results:
        summary["ot_max_score"] = db_results["opentargets"].get("max_score")
        summary["top_disease"]  = (db_results["opentargets"].get("top_diseases") or [{}])[0].get("disease")

    result = {
        "target":                gene_or_drug,
        "databases_queried":     list(db_results.keys()),
        "evidence_sources":      evidence_sources,
        "target_richness_score": round(evidence_sources / max(len(dbs), 1), 2),
        "summary":               summary,
        "details":               db_results,
    }
    SESSION.setdefault("variant_effects", [])
    return result
