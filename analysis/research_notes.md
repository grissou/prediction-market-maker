# Section 7 research notes (sonnet helper, 2026-10-01 ~21:40 UTC)
Proxy blocked direct fetches of predictionscup.com, sig.thesuper.market, sig.com, businesswire.com, docs.polymarket.com: quotes below are from search-result text ("search-summary") unless marked fetched. Verify on the live pages.
- Rules: https://predictionscup.com/rules/ ; platform https://sig.thesuper.market/ ; docs https://sig.thesuper.market/docs ; press release https://www.businesswire.com/news/home/20260922468550/en/Susquehanna-Predictions-Launches-Predictions-Cup-for-University-Students
- "runs from October 1, 2026 noon ET to November 4, 2026 noon ET."
- "the top ranked user will win $30,000. Second place will win $5,000 and third place will win $2,500."
- "Users all begin each competition with 100,000 SUSQies. You must complete at least one trade to receive a rank. ... the ranking at the end of the competition is fully determined by your final SUSQie balance"
- "Upon the official resolution of each Market, the Platform will adjust each participant's SUSQies balance by crediting or debiting SUSQies as applicable."
- Bots allowed: participants "may connect an unlimited number of automated software programs ('Bots') to their Account, provided that each Participant maintains only one (1) Account" ... "subject to applicable authentication requirements, rate limits, position limits, technical controls"
- Sponsor may "disqualify any participant, void or reverse any trade, adjust any SUSQies balance or leaderboard standing" for violations or "unsportsmanlike or disruptive manner".
- Smart Score: no public definition found. Press release: competitions are "part of how Susquehanna identifies students with an aptitude for probabilistic thinking".
- SIG API limits (third-party repo quoting docs, fetched https://github.com/nullif1ed/sigprediction): "100 reads and 30 writes per minute per key; a 429 response carries `Retry-After: 60`." Bulk /exchanges/prices; realtime websocket pushes full books without using read budget.
- Polymarket (third-party summaries, e.g. https://www.polytest.io/blog/polymarket-api-rate-limits): gamma ~4,000 req/10 s general, /markets 300/10 s, /events 500/10 s; CLOB ~9,000/10 s; Cloudflare throttles (queues) rather than rejects.
- NOT found: wash/spoof/collusion wording; valuation of unresolved positions at the 4 Nov end; leaderboard mark price; position-limit numbers; Smart Score formula; 409 semantics.
