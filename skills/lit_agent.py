import arxiv
from semanticscholar import SemanticScholar
import json

class LiteratureSpecialist:
    def __init__(self):
        self.sch = SemanticScholar()

    def discover_breakthroughs(self, drug_name, indication, limit=3):
        query = f"{drug_name} {indication}"
        print(f"📖 [RESEARCHER] Investigating Open-Source Literature for {query}...")
        
        # 1. Search ArXiv for latest Pre-prints (Physics/Bio/Math)
        search = arxiv.Search(query=query, max_results=limit, sort_by=arxiv.SortCriterion.PublishedDate)
        arxiv_results = []
        for res in search.results():
            arxiv_results.append({"source": "ArXiv", "title": res.title, "link": res.pdf_url})

        # 2. Search Semantic Scholar for Peer-Reviewed Evidence
        # Note: Semantic Scholar search_paper returns a list of results directly
        results = self.sch.search_paper(query, limit=limit)
        scholar_results = []
        for paper in results:
            scholar_results.append({
                "source": "Semantic Scholar",
                "title": paper.title,
                "year": paper.year,
                "citations": paper.citationCount,
                "venue": paper.venue if paper.venue else "Journal Unknown"
            })

        return {"arxiv": arxiv_results, "scholar": scholar_results}

if __name__ == "__main__":
    agent = LiteratureSpecialist()
    # Let's check for the Giredestrant/Prostate cancer gap we found
    findings = agent.discover_breakthroughs("Giredestrant", "prostate cancer")
    print(json.dumps(findings, indent=2))
