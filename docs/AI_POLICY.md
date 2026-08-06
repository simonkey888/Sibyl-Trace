# GPT advisory policy

The model is a read-only analyst, not a trader.

## Allowed

- Summarize source quality and portfolio state.
- Detect concentration, drift, latency, rejection, and slippage patterns.
- Recommend pausing, investigating, gathering more evidence, or retaining controls.

## Prohibited by architecture

- Accessing a wallet private key or CLOB credentials.
- Calling an order, signer, transfer, withdrawal, or balance-mutation tool.
- Overriding wallet selection or deterministic risk limits.
- Claiming profitability from limited paper evidence.
- Promoting the runtime to LIVE.

## Privacy and cost controls

- Wallet addresses are replaced with stable 12-character hashes before model submission.
- Transaction hashes are excluded.
- Responses use `store: false`.
- Identical evidence hashes are not analyzed twice.
- Analysis runs no more often than the configured interval; default is six hours.
- The layer is disabled unless explicitly enabled and supplied an API key.
