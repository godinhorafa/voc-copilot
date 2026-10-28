# Retrieval eval: golden-telecom-seed42-n500

- Strategy: `vector`; embeddings `fake-embedding`
- Questions: 45; k = 10
- Generated: 2026-10-28T18:33:23+00:00

| Questions | n | MRR@10 | Precision@10 | Recall@10 |
|-----------|--:|-------:|-------------:|----------:|
| **all** | 45 | 0.387 | 0.240 | 0.268 |
| churn_month | 4 | 0.119 | 0.050 | 0.087 |
| competitor | 3 | 0.556 | 0.400 | 0.400 |
| event | 2 | 0.625 | 0.400 | 0.411 |
| reason_month | 36 | 0.390 | 0.239 | 0.269 |

## Hardest questions

- `reason_month:device_support:2026-07`: recall 0.00, MRR 0.00 (13 relevant)
- `reason_month:device_support:2026-08`: recall 0.00, MRR 0.00 (10 relevant)
- `reason_month:device_support:2026-09`: recall 0.00, MRR 0.00 (8 relevant)
- `reason_month:device_support:2026-10`: recall 0.00, MRR 0.00 (10 relevant)
- `reason_month:device_support:2026-11`: recall 0.00, MRR 0.00 (6 relevant)
