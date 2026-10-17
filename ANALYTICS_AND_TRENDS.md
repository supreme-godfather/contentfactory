# Analytics and trend selector

The factory now has separate analytics and discovery layers.

## Trend selector

Trend candidates are scored from:

- freshness: 30%
- channel relevance: 35%
- originality: 25%
- risk penalty: 10%

The selector stores the source, source URL, scores, status, and timestamp. It
does not scrape private data, copy another creator's content, or automatically
publish a trend. A high score is a queue recommendation, not permission to
publish.

Future official adapters can import public trend signals from approved sources
such as YouTube/Google APIs, Meta insights where permitted, RSS feeds, and
operator-provided topics. Each adapter must have its own credentials, rate
limits, attribution, and failure handling. No unofficial Instagram scraping or
browser-cookie reuse is allowed.

## Analytics

Analytics snapshots support views, likes, comments, shares, watch time, and
engagement rate per platform/account/content ID. Official YouTube Analytics and
Meta Insights adapters will populate these records after you configure their
OAuth permissions.

The engine will use analytics to recommend:

- topic categories
- duration ranges
- posting windows
- hooks and formats
- brand allocation

It will not guarantee viral growth and will not silently change strategy after
one anomalous result.
