# SafeNestT-style investigation tools for MIA-RZ

This integration ports the investigation workflow pattern (validation → collection → evidence normalization → correlation → assessment → report) without importing SafeNestT's production database, case store, API keys, or model configuration.

## Optional provider environment variables

| Variable | Tool capability | Required |
|---|---|---|
| `DEHASHED_API_KEY` + `DEHASHED_EMAIL` | DeHashed exposure search | Both |
| `SHODAN_API_KEY` | Shodan domain/IP lookup | Optional |
| `VT_API_KEY` | VirusTotal domain/IP lookup | Optional |
| `HIBP_API_KEY` | Have I Been Pwned email breach lookup | Optional |
| `HUNTER_API_KEY` | Hunter email verification/domain search | Optional |
| `FIRECRAWL_API_KEY` | Firecrawl URL scraping | Optional |
| `ETHERSCAN_API_KEY` | Ethereum wallet/transaction lookup | Optional |

DNS and reverse DNS do not require these provider keys. Public-source web search uses MIA-RZ’s existing search implementation; configure `BRAVE_SEARCH_API_KEY` for authenticated search or rely on its bounded public-search fallback. Missing provider credentials are reported as `not_configured`; the tool never claims the target is clean because a lookup was unavailable. Add credentials only to the RZ runtime's secret/environment settings; never commit real secrets or copy SafeNestT production values. `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, and `DATABASE_URL` are not changed by this integration.

## Model tool

`mia_safenestt_investigate` runs the pipeline from MIA's bounded tool loop. Indicators are restricted to `email`, `domain`, `ip_address`, `username`, `phone`, `url`, `wallet_address`, and `transaction_hash`. Tool outputs are untrusted external data; findings require human review.

## Known scope limits

- The public paste search is a public-web index search, not authenticated dark-web access.
- Etherscan support in this initial port is Ethereum only.
- Provider credentials and quotas determine which external adapters can return data.
- The presentation UI and advanced entity/relationship parsing are intentionally deferred until successful tool execution is verified.
