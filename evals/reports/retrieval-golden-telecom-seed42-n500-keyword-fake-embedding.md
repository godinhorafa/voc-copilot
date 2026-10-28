# Retrieval eval: golden-telecom-seed42-n500

- Strategy: `keyword`; embeddings `fake-embedding`
- Questions: 45; k = 10
- Generated: 2026-10-28T18:49:18+00:00

| Questions | n | MRR@10 | Precision@10 | Recall@10 |
|-----------|--:|-------:|-------------:|----------:|
| **all** | 45 | 0.826 | 0.656 | 0.768 |
| churn_month | 4 | 1.000 | 0.525 | 0.950 |
| competitor | 3 | 0.708 | 0.600 | 0.600 |
| event | 2 | 1.000 | 0.550 | 0.567 |
| reason_month | 36 | 0.806 | 0.681 | 0.773 |

## Hardest questions

- `reason_month:technical_issue:2026-09`: recall 0.10, MRR 0.50 (16 relevant)
- `reason_month:roaming:2026-09`: recall 0.17, MRR 0.12 (6 relevant)
- `reason_month:roaming:2026-06`: recall 0.20, MRR 0.14 (10 relevant)
- `reason_month:roaming:2026-10`: recall 0.29, MRR 0.11 (7 relevant)
- `competitor:SkyLink Mobile`: recall 0.30, MRR 0.12 (18 relevant)
