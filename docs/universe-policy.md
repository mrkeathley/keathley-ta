# Universe policy

## Configuration, not conviction in code

The product has no built-in investment symbols. `KTA_UNIVERSE` is required for a configured run, and
`kta run --universe ...` can override it for one cycle. Changing a thesis or watchlist therefore does not
require a code change or release.

The current strategy only lets an agent narrow deterministic signals from this configured set. An agent
cannot invent a new symbol during a decision call. Held positions are always scanned even after removal
from the entry watchlist so risk management is not accidentally disabled.

`kta smoke` uses obviously synthetic placeholder names in a temporary journal. Those names are test
inputs, not portfolio recommendations or runtime defaults.

## Thematic mandate

The research mandate remains deliberately broad: technology with particular attention to AI-enabling
infrastructure, upstream constraints, downstream adoption, adjacent power and communications systems,
and other technically grounded high-conviction themes. Congressional disclosures, attention data, and
catalyst research are evidence features. None is itself an eligibility rule or automatic trade trigger.

## Planned dynamic construction

A later milestone should create versioned universe snapshots before the daily signal scan. Construction
should be deterministic and auditable:

1. Start from documented exchange/security-type eligibility rules.
2. Apply minimum price, liquidity, data-quality, and tradability requirements.
3. Attach theme/entity classifications with sources and effective timestamps.
4. Rank candidates from point-in-time research features without using future information.
5. Record additions, removals, reasons, and the full snapshot in the journal.
6. Keep discovery separate from the scout: discovery proposes eligible symbols; the strategy generates
   signals; the agent can only reject or size within those candidates.

Until that pipeline and its historical evaluation exist, a human-maintained deployment watchlist is the
honest boundary. It avoids pretending that a few symbols embedded in source code are a reproducible
investment thesis.
