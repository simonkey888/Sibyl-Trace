# SIBYL_PAPER_V2 Freeze Amendment 001

- Scope: transport-only correction for the BTC Latency Lab.
- Trigger: GitHub-hosted PAPER evidence observed HTTP 451 from Binance's global WebSocket host.
- Change: use Binance's officially documented market-data-only WebSocket host for the existing public `btcusdt@aggTrade` feed.
- Strategy logic: unchanged.
- Risk policy: unchanged (`RISK_V1_FROZEN`).
- Scoring: unchanged (`SCORE_V2`).
- Simulator: unchanged (`PAPER_SIM_V2`).
- Evidence generation: unchanged (`SIBYL_PAPER_V2`); prior evidence remains preserved and is not rewritten.
- LIVE/real-money capability: unchanged and absent.
- Cost authorization: unchanged at USD 0.
- Credentials/authentication: none added; the feed is public market-data only.
- Protected baseline: refreshed after preregistration; `latency.py` remains protected rather than being exempted from the freeze guard.
- Validation requirement: pass exact-head CI, then require two consecutive state-preserving PAPER runs on canonical `main` with Binance public-feed observations before closing the amendment.
