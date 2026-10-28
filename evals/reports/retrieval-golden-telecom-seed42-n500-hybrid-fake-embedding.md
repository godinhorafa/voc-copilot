# Retrieval eval: golden-telecom-seed42-n500

- Strategy: `hybrid`; embeddings `fake-embedding`
- Questions: 45; k = 10
- Generated: 2026-10-28T18:49:20+00:00

| Questions | n | MRR@10 | Precision@10 | Recall@10 |
|-----------|--:|-------:|-------------:|----------:|
| **all** | 45 | 0.722 | 0.520 | 0.594 |
| churn_month | 4 | 0.667 | 0.375 | 0.650 |
| competitor | 3 | 0.778 | 0.767 | 0.767 |
| event | 2 | 1.000 | 0.650 | 0.667 |
| reason_month | 36 | 0.709 | 0.508 | 0.570 |

## Hardest questions

- `reason_month:roaming:2026-06`: recall 0.00, MRR 0.00 (10 relevant)
- `reason_month:roaming:2026-07`: recall 0.00, MRR 0.00 (3 relevant)
- `reason_month:roaming:2026-08`: recall 0.00, MRR 0.00 (7 relevant)
- `reason_month:roaming:2026-09`: recall 0.00, MRR 0.00 (6 relevant)
- `reason_month:roaming:2026-10`: recall 0.00, MRR 0.00 (7 relevant)
