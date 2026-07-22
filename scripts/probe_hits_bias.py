# scripts/probe_hits_bias.py
import pandas as pd
p = pd.read_csv(r"data\models\gbm\wf_predictions_catboost.csv", low_memory=False)
h = p[p.category == "hits"]
print(f"n = {len(h)}")
print(f"mean predicted hits: {h.predicted_value.mean():.4f}")
print(f"mean actual hits:    {h.actual_value.mean():.4f}")
print(f"mean error:          {h.predicted_value.mean() - h.actual_value.mean():+.4f}")
print()
print("actual hits distribution:")
print(h.actual_value.value_counts().sort_index().to_string())
