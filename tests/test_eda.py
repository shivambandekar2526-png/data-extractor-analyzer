import os
import pandas as pd
from core.eda_engine import EDAEngine
from core.ai_insights import AIInsightsSynthesizer

# Remove old cache to refresh
cache_file = "knowledge_base/semantic_map_cache.json"
if os.path.exists(cache_file):
    os.remove(cache_file)

import unittest

class TestEDAEngine(unittest.TestCase):
    def test_eda_engine_analysis(self):
        csv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "output", "2122-5th", "2122-5th_extracted_data.csv"))
        if not os.path.exists(csv_path):
            alt_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data_extractor", "output", "2122-5th", "2122-5th_extracted_data.csv"))
            if os.path.exists(alt_path):
                csv_path = alt_path

        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
            engine = EDAEngine()
            res = engine.run_analysis(df)
            self.assertIn("overview", res)
            self.assertIn("subject_analytics", res)
            self.assertIn("demographics", res)

            synthesizer = AIInsightsSynthesizer()
            insights = synthesizer.generate_narrative(res)
            self.assertIn("engine_used", insights)
            self.assertIn("narrative_markdown", insights)

if __name__ == "__main__":
    unittest.main()
