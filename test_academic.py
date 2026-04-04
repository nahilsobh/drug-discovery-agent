import arxiv
from semanticscholar import SemanticScholar
from habanero import Crossref
import json

def test_academic_connectivity():
    print("🧪 --- ACADEMIC INTELLIGENCE AUDIT (MAR 2026) ---")
    
    # 1. Test ArXiv (Updated to Client Pattern)
    print("\n📡 Testing ArXiv...")
    try:
        # Construct the default API client (2026 Standard)
        client = arxiv.Client()
        search = arxiv.Search(query="Giredestrant breast cancer", max_results=1)
        
        # Use client.results() which returns a generator
        results = list(client.results(search))
        if results:
            print(f"✅ ArXiv: ONLINE (Found: {results[0].title[:50]}...)")
        else:
            print("⚠️  ArXiv: ONLINE (No results found for query)")
    except Exception as e:
        print(f"❌ ArXiv: FAILED ({e})")

    # 2. Test Semantic Scholar (Result Object Fix)
    print("\n📡 Testing Semantic Scholar...")
    try:
        sch = SemanticScholar()
        # search_paper returns a PaginatedResults object
        results = sch.search_paper("ESR1 mutation Giredestrant", limit=1)
        
        # Access the 'total' attribute to check for results
        if results.total > 0:
            print(f"✅ Semantic Scholar: ONLINE (Found: {results[0].title[:50]}...)")
        else:
            print("⚠️  Semantic Scholar: ONLINE (But 0 papers found)")
    except Exception as e:
        print(f"❌ Semantic Scholar: FAILED ({e})")

    # 3. Test Crossref (Metadata Verification)
    print("\n📡 Testing Crossref...")
    try:
        cr = Crossref()
        res = cr.works(query="Giredestrant", limit=1)
        title = res['message']['items'][0].get('title', ['Unknown'])[0]
        print(f"✅ Crossref: ONLINE (Found: {title[:50]}...)")
    except Exception as e:
        print(f"❌ Crossref: FAILED ({e})")

if __name__ == "__main__":
    test_academic_connectivity()
