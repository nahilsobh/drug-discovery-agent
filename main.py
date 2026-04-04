import json
import os
import sys

# Ensure the 'skills' directory is in the python path for local imports
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

try:
    from skills.pdf_generator import PDFDossierAgent
except ImportError:
    print("❌ Error: Could not import PDFDossierAgent from skills/pdf_generator.py")
    print("Ensure the file exists and the class name is exactly 'PDFDossierAgent'.")
    sys.exit(1)

class RocheAIOrchestrator:
    def __init__(self, cache_file="knowledge_base/intelligence_cache.json"):
        self.cache_file = cache_file
        self.output_pdf = "Roche_Strategic_Expert_Dossier_2026.pdf"

    def run_synthesis(self):
        """
        Reads the deep-mined intelligence and generates the final expert dossier.
        """
        print(f"🎨 [ORCHESTRATOR] Initiating Final Synthesis for Roche 2030 Strategy...")

        if not os.path.exists(self.cache_file):
            print(f"❌ Error: {self.cache_file} not found.")
            print("Run 'python3 extract_all_data.py' to mine the evidence first.")
            return

        # 1. Load the Intelligence Cache (Genomics, Clinical, and Literature data)
        try:
            with open(self.cache_file, "r") as f:
                portfolio_data = json.load(f)
        except Exception as e:
            print(f"❌ Error reading intelligence cache: {e}")
            return

        # 2. Initialize the PDF Agent (Handles multi-line wrapping and formatting)
        pdf_agent = PDFDossierAgent(self.output_pdf)
        
        print(f"📊 Synthesizing {len(portfolio_data)} asset profiles with full evidence...")

        # 3. Process each asset into a dedicated evidence-linked page
        for asset in portfolio_data:
            name = asset.get('name', 'Unknown Asset')
            print(f"✨ Finalizing Evidence Page: {name}")

            # Map the cache data to the PDF Agent's requirements
            # We include the new 'evidence_title' and 'doi' fields for the Bibliography
            asset_payload = {
                "name": name,
                "id": asset.get('id', 'N/A'),
                "top_disease": asset.get('top_disease', 'N/A'),
                "score": asset.get('score', 0.0),
                "trials": asset.get('trials', 0),
                "nct_id": asset.get('nct_id', 'None Found'),
                "evidence_title": asset.get('evidence_title', 'Literature Sweep Pending...'),
                "doi": asset.get('doi', 'N/A')
            }

            # Generate the individual page
            pdf_agent.create_asset_page(asset_payload)

        # 4. Save and Finalize
        try:
            pdf_agent.save()
            print("\n" + "="*60)
            print(f"✅ [SUCCESS] Strategic Portfolio Audit Complete.")
            print(f"📂 Report: {os.path.abspath(self.output_pdf)}")
            print("="*60)
        except Exception as e:
            print(f"❌ Error finalizing PDF: {e}")

if __name__ == "__main__":
    orchestrator = RocheAIOrchestrator()
    orchestrator.run_synthesis()
