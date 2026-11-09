# Amazon Video Games Explainable Recommendation System

Repository: https://github.com/tisha-varma/Amazon-Recommendation-System
Live demo: https://amazon-recommendation-system-games.streamlit.app/

Tagline: Comparing SVD, KNN, NMF with Explainable AI Recommendations.

Repository: public, 16 commits, MIT license badge. LinkedIn post: 481 impressions.

Stack: Python 3.12, scikit-surprise 1.1.5, pandas, scikit-learn, numpy, matplotlib, seaborn, Streamlit, Hugging Face datasets.

Idea: compare three collaborative-filtering algorithms on real Amazon data and give a human-readable reason for every recommendation.

Data: Amazon Reviews 2023, Video Games category. 1,000,000 raw reviews sampled (streamed from 2.8M total, no full download), filtered to 128,687 interactions from 14,839 users and 2,206 items; 99.61% sparse; 8.7 ratings per user and 58.3 ratings per item on average. Filtering iteratively removes users with fewer than 5 ratings and items with fewer than 20 ratings until stable (22 iterations).

Split: temporal 80% train / 10% validation / 10% test, so the model never trains on the future.

Models: SVD (100 factors, 20 epochs), item-based KNN (cosine, 40 neighbours), NMF (50 factors, 50 epochs).

Metrics: RMSE, Precision@K, Recall@K, NDCG@K; a "liked" item is a rating of 4 or higher.

## Results (from the repository README)

| Model | RMSE | P@5 | P@10 | R@10 | NDCG@10 |
| --- | --- | --- | --- | --- | --- |
| SVD | 1.1911 | 0.3481 | 0.1954 | 0.7443 | 0.8081 |
| KNN | 1.2527 | 0.3538 | 0.2004 | 0.7633 | 0.8053 |
| NMF | 1.2760 | 0.3514 | 0.1996 | 0.7642 | 0.8046 |

SVD is best on RMSE and NDCG@10, KNN on Precision@5 and @10, NMF on Recall@10. The best rating predictor is not always the best recommender.

## Explainability

- KNN: true explanation by tracing which of the user's highly rated items are neighbours of the recommended item.
- SVD and NMF: approximate explanation through co-purchase patterns (users who rated this item highly also rated X highly), since latent factors are not human-readable.

Features: dark-themed Streamlit app with personalised recommendations, cold-start handling (fallback to popular items), user history visualisation and live algorithm comparison.

Exploratory findings: 64% of reviews are 5-star; the top 10% of items receive over 60% of ratings; the matrix is 3.21% filled; review volume peaked in 2018-2020.

Stated limitations: optimistic rating bias, cold start, a limited item pool, approximate explanations for SVD and NMF.

Pipeline: preprocess.py -> train.py -> evaluate.py / explain.py -> recommend.py -> app/streamlit_app.py.
