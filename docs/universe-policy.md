# Universe policy

## Research output, not configuration

The product has no built-in investment symbols. The human-owned `config/mandate.toml` describes themes,
regions, asset classes, exclusions, and time horizon. Continuous discovery searches beyond obvious names,
an independent supervisor reviews every proposal, and the journal records immutable universe snapshots.
Before activation, each symbol must also resolve through the read-only Alpaca asset directory as an active,
tradable US equity/ETF and pass configured completed-bar minimum price and average dollar-volume gates.
`KTA_UNIVERSE` remains only as optional seed context and as an offline compatibility mode.

Discovery may propose new symbols. Trade-decision agents remain limited to the latest supervised snapshot;
they cannot introduce a symbol while allocating capital. Held positions are always scanned even after
removal from the active universe so risk management is not accidentally disabled. Research evidence is
journaled per symbol, even when the provider retrieved a candidate batch in one request.

`kta smoke` uses obviously synthetic placeholder names in a temporary journal. Those names are test
inputs, not portfolio recommendations or runtime defaults.

## Thematic mandate

The research mandate remains deliberately broad: technology with particular attention to AI-enabling
infrastructure, upstream constraints, downstream adoption, adjacent power and communications systems,
and other technically grounded high-conviction themes. Congressional disclosures, attention data, and
catalyst research are evidence features. None is itself an eligibility rule or automatic trade trigger.

## Dynamic construction

Each refresh creates a versioned snapshot before queueing its signal scan:

1. Start from documented exchange/security-type eligibility rules.
2. Apply minimum price, liquidity, data-quality, and tradability requirements.
3. Attach theme/entity classifications with sources and effective timestamps.
4. Rank candidates from point-in-time research features without using future information.
5. Record additions, removals, reasons, and the full snapshot in the journal.
6. Keep discovery separate from the portfolio decision: discovery proposes eligible symbols; the strategy generates
   signals; the agent can only reject or size within those candidates.
