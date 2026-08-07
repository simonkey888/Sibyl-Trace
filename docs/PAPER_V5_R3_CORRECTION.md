# PAPER V5 R3 — intracycle accounting correction

R2 proved that real public decision books, arrival books, fee metadata and L2 FAK fills were reachable. Its first clean run produced real simulated fills with book hashes and fees, but it also exposed a methodological bug in the intracycle risk state.

After a BUY fill, R2 updated cash and shares immediately but left the new position `mark_value_usd=0` until the end-of-cycle mark pass. During the remaining signal loop this temporarily made invested cash look like a realized economic loss. The frozen risk policy could therefore trip `daily_loss_limit` even though the position still had executable bid value. R2 is retained as audit evidence, not as the final canonical performance cohort.

R3 fixes this without rewriting R2 or V2 history:

- each successful BUY/SELL fill is immediately marked to net executable liquidation value using the same observed arrival book;
- the normal end-of-cycle mark pass still refreshes the position later;
- a public CLOB book HTTP 404 is classified as `NO_FILL` (`decision_book_not_found` or `arrival_book_not_found`), not an adapter failure;
- systemic adapter health remains fail-closed for genuine market-data/arrival transport or parsing failures;
- `itode=true` receives 250 ms only for the observed crypto-style fee contract (`r=0.07`); any other delayed-market schedule fails closed instead of guessing;
- R3 starts with a fresh rolling state tag and a clean $300 cohort.

Canonical cohort ID: `PAPER_V5_R3_INTRACYCLE_MARK_2026_08_07`.

The same hard safety boundary remains: PAPER only, no order placement, no signing/private keys, no real money, no paid APIs, authorized cost $0.