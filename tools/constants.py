import os

SPONSORS    = ["Hoffmann-La RedClaw", "RedClaw, Inc."]
CT_URL      = "https://clinicaltrials.gov/api/v2/studies"
OT_URL      = "https://api.platform.opentargets.org/api/v4/graphql"
CACHE_PATH  = "knowledge_base/intelligence_cache.json"
UNIPROT_URL = "https://rest.uniprot.org/uniprotkb/search"
OPENFDA_URL = "https://api.fda.gov/drug/drugsfda.json"
MODEL       = os.environ.get("AGENT_MODEL", "claude-opus-4-6")
MAX_TURNS   = int(os.environ.get("AGENT_MAX_TURNS", "20"))

CLAWAPI_URL   = os.environ.get("CLAWAPI_URL", "http://127.0.0.1:8083")
GENOMECLAW_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "genomeclaw")

COMPETITORS = [
    "AstraZeneca", "Eli Lilly", "Novartis", "Pfizer",
    "Merck", "Bristol-Myers Squibb", "AbbVie", "Johnson & Johnson",
]

ARXIV_CATS = {
    "q-bio.BM", "q-bio.GN", "q-bio.QM", "q-bio.TO", "q-bio.NC", "q-bio.OT",
    "cs.LG", "cs.AI", "stat.ML", "physics.bio-ph",
}

PREVALENCE_MAP = {
    "fanconi anemia":                {"prevalence": 1400},
    "huntington":                    {"prevalence": 30000},
    "friedreich ataxia":             {"prevalence": 15000},
    "pompe disease":                 {"prevalence": 10000},
    "gaucher disease":               {"prevalence": 20000},
    "niemann-pick":                  {"prevalence": 1800},
    "fabry disease":                 {"prevalence": 50000},
    "cystic fibrosis":               {"prevalence": 35000},
    "duchenne muscular dystrophy":   {"prevalence": 15000},
    "spinal muscular atrophy":       {"prevalence": 20000},
    "amyotrophic lateral sclerosis": {"prevalence": 16000},
    "multiple myeloma":              {"prevalence": 160000},
    "hairy cell leukemia":           {"prevalence": 15000},
    "waldenstrom macroglobulinemia": {"prevalence": 17000},
    "primary sclerosing cholangitis":{"prevalence": 30000},
    "wilson disease":                {"prevalence": 10000},
    "phenylketonuria":               {"prevalence": 14000},
    "hereditary angioedema":         {"prevalence": 10000},
    "tuberous sclerosis":            {"prevalence": 50000},
    "transthyretin amyloidosis":     {"prevalence": 50000},
}
