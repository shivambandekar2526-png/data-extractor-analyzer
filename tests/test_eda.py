import os
import pandas as pd
from core.eda_engine import EDAEngine
from core.ai_insights import AIInsightsSynthesizer

# Remove old cache to refresh
cache_file = "knowledge_base/semantic_map_cache.json"
if os.path.exists(cache_file):
    os.remove(cache_file)

csv_path = "../data_extractor/output/2122-5th/2122-5th_extracted_data.csv"
df = pd.read_csv(csv_path)
print(f"Loaded {len(df)} rows from {csv_path}")

engine = EDAEngine()
res = engine.run_analysis(df)
print("\n--- Cohort Overview ---")
for k, v in res["overview"].items():
    print(f"  {k}: {v}")

print("\n--- Subject Analytics ---")
for s in res["subject_analytics"]:
    print(f"  {s['code']:<10} | {s['name'][:30]:<30} | Pass: {s['pass_pct']}% | Avg: {s['avg_marks']} | SE: {s['avg_se']} | IA: {s['avg_ia']} | TW: {s['avg_tw']}")

print("\n--- Demographics ---")
print(res["demographics"])

print("\n--- AI Narrative Test ---")
synthesizer = AIInsightsSynthesizer()
insights = synthesizer.generate_narrative(res)
print(f"Engine used: {insights['engine_used']}")
print(f"Tokens consumed: {insights['tokens_consumed']}")
print("\nExecutive Summary:\n" + insights["narrative_markdown"][:300] + "...")
