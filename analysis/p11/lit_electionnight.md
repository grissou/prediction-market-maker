# P11 literature sweep, reader 5: ELECTION NIGHT AND IN-PLAY TRADING (theme tag EN)
Reader 5, 4 Oct 2026, 12:45-14:15 UTC. About 45 web searches (the session's search budget then ran out) and ~45 fetches; 31 sources
read at least at abstract/summary level, 8 could not be read (listed at the end). Read-only look at `mm_bot.py` (take path, close
handling) and `analysis/p9/ideas_D.md` D-12, `lit_forecasting.md` FC-1/FC-9/FC-13. No live_sim, no tests, no repo change except this file.

**The question this file prepares for (LIT_REVIEW A6).** The rules say "All trades must be received prior to 12:00pm Eastern Standard
Time on November 4, 2026" (= **17:00 UTC 4 Nov**). The API market objects say `settlementDate 2026-11-04T00:00:00Z`. Every idea below is
written for BOTH cases:
- **Case A (CLOSED at 00:00 UTC):** the last 2 hours (22:00-00:00 UTC) contain exit-poll chatter, the first raw votes from eastern
  Kentucky/Indiana (23:00 UTC) and nothing called. The job is defence: no stale quote picked off, no take on a noisy reference.
- **Case B (OPEN to 17:00 UTC):** 17 hours of public results, ~1,040 players' resting orders, Polymarket repricing within minutes,
  most eastern/midwestern races called by AP. The job is an "election-night mode" built on the existing take path.

## 0. Things from our own code that decide how the literature applies (read first)

| Fact (read-only look at `mm_bot.py`) | Why it matters |
|---|---|
| `load_markets` sets each market's close = **min(settlementDate, endDate) = 00:00 UTC** (line 4121); `stop_minutes_before_close` 15 cancels **everything at 23:45 UTC**; takes are skipped inside `close_window("flatten_hours_before_close")` | **Case A is already handled crudely** (all quotes gone at 23:45). **Case B is impossible without a close override**: even if SIG accepts orders all night, our bot will have stopped itself at 23:45 UTC. Also: if SIG drops markets whose settlementDate has passed from `markets()`, `load_markets` "cancels and forgets" them. |
| `execute_take`: tail guard (`tail_high` 0.95 / `tail_low` 0.05) forbids buying YES above fv 0.95 or selling YES below 0.05 except to shrink | The best election-night trade (buy a called winner at 0.90 when Polymarket is 0.995) is **blocked by design**. Needs an override in the mode. |
| `max_order_cash_frac` 0.01 (1k per order), `kelly_max_market_frac` 0.02 (2k at risk per market), `kelly_fraction` 0.25 | Built for 5c pre-election edges; far too small for 10-50c edges on near-certain outcomes. |
| A take costs **3 writes** (cancel own quotes, take, cancel leftovers); budget 28 writes/min | **~9 takes/min max** if nothing else writes. Election night is write-bound for the first 2-3 hours after each poll-closing wave. |
| Reference = Polymarket **gamma** prices, refreshed every 5 s (`ref_refresh_seconds`), `take_confirm_seconds` 30, `take_ref_max_age_seconds` 30; jump guard pulls our quotes 60 s on a 3c move | The defensive tools exist. But Polymarket race markets **resolve and close** once AP, Fox and NBC all call (rule text below): a closed market may drop out of the reference (`liquid` filter) exactly when it is most valuable (p = 1). |
| SIG changelog: "API market data may be up to two seconds old"; "live updates that can't be delivered promptly are now dropped" | On a busy night the realtime feed will drop updates: always re-download the book before a take (the take path already does). |
| Tilt correction (`ref_tilt_enabled`, tilt ~14%) | Meaningless once votes are counted: the reference for takes must be raw Polymarket (`take_tilted_ref` off). |

## 1. Poll-closing and race-call timeline in UTC (3-4 Nov 2026; DST ends 1 Nov, so ET = UTC-5)

| UTC | ET | What closes / happens | What it means for our markets |
|---|---|---|---|
| ~21:00-22:00 3 Nov | 4-5 pm | National Election Pool exit-poll first wave goes to newsrooms (quarantined to ~5 pm); historically leaks (2004) | Noise. 2004: leaked exits moved Bush 55% -> 30% on Tradesports, back to 95% by midnight (Snowberg-Wolfers-Zitzewitz). Do NOT take on moves in this window. |
| **23:00** | 6 pm | Most of **Indiana and Kentucky** (Eastern-time counties) close; first raw counts within ~10-20 min | Only KY Senate, KY House seats and any IN seats get real votes inside case A (p9 D-12). Kenton (KY) / Hamilton, Vigo (IN) are the swing read. |
| **00:00 4 Nov** | 7 pm | **GA, VA, VT, SC, most of FL**, rest of IN/KY; **case A trading ends here** | AP calls lopsided races at poll close (VT, SC, KY Senate if VoteCast agrees). FL and GA count fast (FL pre-processes mail 22 days early). |
| 00:30 | 7:30 pm | **NC, OH, WV** | Ohio: first votes ~9 min after close, early votes lean D then erode over ~2 h (AP Decision Notes); ~95% counted by 05:00 UTC in 2020. |
| 01:00 | 8 pm | **PA, MI (most), ME, NH, MA, NJ, CT, RI, DE, MD, DC, IL, MO, MS, AL, OK, TN, most of TX and KS**, FL panhandle | The big wave. PA/MI count mail slowly (PA cannot pre-process). |
| 01:30 | 8:30 pm | AR | |
| 02:00 | 9 pm | **NY, MN, WI, IA, CO, AZ, NM, NE, LA, WY**, rest of TX/KS/MI/SD/ND | AZ counts for days. |
| 03:00 | 10 pm | **NV, MT, UT, ID (most)** | NV: days (2022 Senate called 12 Nov). |
| 04:00 | 11 pm | **CA, WA, OR**, HI (05:00) | CA House seats: weeks. |
| 05:00 / 06:00 | 12 / 1 am | **AK** (most / Aleutians) | AK Senate and Governor: RCV tabulation ~15 days later (~18 Nov). |

**Typical call timing (AP and networks):** lopsided races at poll close (AP: ~2,000 uncontested of ~6,500 races in 2024 at or near
close, never a competitive one); safe-but-contested statewide races 1-3 h after close (2016 OH 2.75 h, FL/NC 3.5 h); competitive
statewide races 4-7 h (2016 PA/WI ~3 am ET) or days (2022 AZ 11 Nov, NV 12 Nov; 2022 House control 16 Nov); 2024 Ohio Senate 04:28 UTC
(11:28 pm ET, >90% counted, margin ~5 pts). Polymarket hits 95-99% **hours before** the AP call (2024: 99.1% Trump at 06:30 UTC; AP
called at 10:34 UTC). **By 17:00 UTC 4 Nov** expect: most East/South/Midwest races called; AZ, NV, CA, AK, ME (RCV), anything within
~1 pt, and GA races under 50% (runoff 1 Dec) not called.

## 2. Sources (what each says that matters to us)

**Election-night price paths**
1. **Snowberg, Wolfers & Zitzewitz, "Partisan impacts on the economy: evidence from prediction markets and close elections" (QJE 2007; IZA
   DP 1996, https://docs.iza.org/dp1996.pdf).** 2004 Tradesports Bush contract: ~55% at noon ET; leaked (wrong) exit polls ~3 pm ET took it
   to ~30%; real counts took it to ~95% by midnight. Markets track news in 10-minute bins. **The canonical exit-poll overreaction then reversal.**
2. **Fortune, 5 Nov 2020 (https://fortune.com/2020/11/05/trump-biden-betting-odds-chances-markets-election-predictions).** Biden ~67% on
   Betfair at the East-coast closes; Florida trending R by ~7:45 pm ET put Trump at ~40%, then ~80% (1-to-4) overnight on the "red mirage"
   (Election-Day votes first in PA/MI/WI); reversal as Milwaukee/Detroit mail came in and AP called Arizona; Biden 86-90% by the next days.
   **Even the deepest political market overshot by ~40 points on order-of-count effects.**
3. **Forbes, 5-6 Nov 2024 (Saul).** ~60/40 Trump at 6 pm ET; 1:30 am ET (06:30 UTC): Polymarket 99.1%, Kalshi 99%, PredictIt 98% while
   most swing states were uncalled; AP called at 5:34 am ET (10:34 UTC). Markets ~4 h ahead of the call, and right.
4. **Polymarket Oracle, "Over Before Midnight" (2024, https://news.polymarket.com/p/over-before-midnight).** State markets touched 95%
   "several hours" before AP calls; Arizona touched 95% and then **fell back**: 95% is not a call.
5. **Kalshi News, "Real-time political markets" (https://news.kalshi.com/p/real-time-political-markets).** Bellwether counties (Loudoun VA)
   moved prices past 90% "hours before the pundits". Kalshi now licenses **AP vote counts and race calls** and shows them on-platform
   (Axios, 2 Mar 2026), non-exclusive.
6. **Asterisk, Jeremiah Johnson, "Prediction markets have an elections problem" (2023).** Weeks after the 2020 calls, PredictIt had
   Biden at ~90c in GA/MI/AZ/PA; "Trump +280 EV" at 8c. Identity bettors + capital limits keep losing sides rich **after** results.
7. **Singh, PredictIt 2020 inefficiencies (socialscience.international).** GA Biden 88c on 22 Nov (called 13 Nov, certified 20 Nov);
   MI Biden 90c after its 4 Nov call; non-monotone EV-margin ladders. The $850 cap stopped arbitrage. **Play money + no cap = our
   tournament is the uncapped version: the edge is cash-limited, not rule-limited.**
8. **The Ringer, "Pins and needles" (7 Nov 2018) + Slate interview with Nate Cohn (2018).** 538's live House model went from >80% D to
   <40% D around 7:40-8:40 pm ET on early returns (KY-06, VA, FL) and recovered within ~1-2 h; the NYT needle had data-pipeline failures
   but "would not have called a single race wrong". **First-hour swing inferences from IN/KY/FL/VA are noisy.**
9. **Rothschild & Sethi, "Trading strategies and market microstructure: evidence from a prediction market" (2016; Intrade 2012).**
   One whale held Romney's price with >40,000-contract walls on election day; at 9 pm ET he withdrew and Intrade "shot up quickly to
   reach the Betfair level". Arbitrageurs were 1% of accounts, 16% of volume. **Stale walls get swept the moment a big player stops.**
10. **Auld & Linton, "The behaviour of betting and currency markets on the night of the EU referendum" (Monash/Cambridge WP 2018).**
    182,534 Betfair trades: a simple model on declared results was 95% Leave by 1:44 am (15th result); Betfair hit 99% ~2 h later (model
    led Betfair by ~113 min), GBP lagged Betfair by ~185 min; ~7% near-riskless return within 2 h. **Even liquid markets underreact to
    sequential counts; a results model beats the market for an hour or two.**
11. **Tsang & Yang, "The anatomy of a blockchain prediction market: Polymarket in 2024" (arXiv 2603.03136) and "Political shocks and price
    discovery" (arXiv 2603.03152).** Cross-venue arbitrage half-lives fell from hours to under a minute by October 2024; the debate shock
    (+11c) mostly reversed, the assassination attempt (+11c) persisted. **Polymarket is fast; its first move on a shock is not always the
    final one.**
12. **Angelini & De Angelis, "When do markets fully process public information? Evidence from real-time prediction markets" (arXiv
    2606.07811, June 2026; Kalshi NBA, 409k one-minute obs).** 0.64-for-1 contemporaneous updating; the gap predicts drift over 5-15 min
    (10-pt gap -> 4.6-pt drift); underreaction is **larger in illiquid markets and for less salient events**. **The tournament crowd, thin
    and slow, should lag Polymarket for minutes to hours; least on the headline races, most on obscure House seats.**

**AP calls, counting, swing**
13. **AP via NPR/WBUR/PBS explainers (2024: https://www.wbur.org/news/2024/10/16/how-ap-calls-election-winners-races,
    https://www.pbs.org/newshour/politics/how-the-ap-is-able-to-declare-winners-in-states-where-polls-just-closed).** AP calls only when
    "the trailing candidate no longer has a path"; poll-close calls only for non-competitive races where VoteCast matches history; "too
    early" (counting) vs "too close" (counting done, margin < ~0.5 pt); may call inside recount range if the raw lead is too big for a
    recount (WI 2020). Accuracy >99.9%; **2 wrong calls of 2,500+ in the 2024 primaries**.
14. **NPR, "AP explains calling Arizona for Biden early" (19 Nov 2020).** AP called AZ at ~2:50 am ET 4 Nov; the lead shrank to ~10,000
    votes (0.3%) because late ballots ran R; AP "would have changed the call" if needed. **The residual risk on a called race is small but
    real and lives in late-counting western states.**
15. **KOLO/AP, "Why AP called the Nevada Senate race" (13 Nov 2022) and AP Ohio Decision Notes (PBS 2024).** NV called 12 Nov after a
    23k-vote Clark batch; Ohio first votes ~9 min after close, early votes lean D and erode over ~2 h, 95% counted by midnight ET;
    recount triggers 0.25% statewide / 0.5% district. Ohio Senate 2024 called 11:28 pm ET (Brown needed 71.9% of the rest).
16. **MIT Election Lab, "Blue shift in the 2020 election" (Stewart 2021) + MIT polisci "How many votes counted after election night".**
    Post-election-night shift ~+1 pt D nationally (2016, 2020) with **rising variance** by state (AK +10.3 D, NJ +3.3 R); within 8 h of
    closing 39 states were within 2 pts of final. PA/MI cannot pre-process mail; FL starts 22 days early. **Early leads in PA/MI/WI are
    R-biased; in OH/NC/FL/GA early leads are D-biased; the West's late ballots now often run R.**
17. **Green Papers 2026 closing times + electiontracker.live 2026 list** (https://www.thegreenpapers.com/G26/closing.phtml,
    https://electiontracker.live/blog/poll-closing-times-2026.html). The table in section 1.
18. **Semafor bellwether hour-by-hour (2024) and Axios "7 bellwethers" (2022).** First reads: Hamilton/Vigo (IN), Kenton (KY) at 6 pm ET;
    DeKalb (GA) at 7; VA-07 (Spanberger) at 7 pm as the 2022 national tell (D held it -> "red wave fizzles", Axios Richmond).
19. **Exit-poll reliability (Baharaeen, substack, + Wikipedia National Election Pool).** 2000 FL (networks called Gore on exits,
    retracted), 2002 (exits pulled), 2004 (Kerry ahead in swing-state exits), 2022 (Edison had abortion 27% vs VoteCast 10%). **Never
    trade exit polls.**
20. **Polymarket 2026 race-market rules (Texas Senate page, read 4 Oct).** "The resolution source ... is the Associated Press, Fox News, and
    NBC. This market will resolve once all three sources call the race for the same candidate", else certification; runoffs included;
    end date 3 Nov (nominal: trading continues until resolution). **After all three call, Polymarket resolves and closes the market: our
    reference for that race disappears or freezes at 1/0.**
21. **Ray Fair, 2022 Senate post-mortem (fairmodel.econ.yale.edu).** The "ranking assumption" held: Democrats lost WI and every state ranked
    below it, none above. **Races fall in order of their pre-election probability; one national swing variable explains most of the
    night.** Supports rho 0.45-0.6 (FC-7) and the "first results reprice the whole ladder" logic.

**In-play microstructure**
22. **Croxson & Reade, "Information and efficiency: goal arrival in soccer betting" (Economic Journal 2014).** On Betfair, "prices update
    swiftly and fully" to goals (the market is suspended at the goal and reopens repriced). **The deep exchange is efficient in seconds;
    the inefficiency is in thin venues and in stale orders.**
23. **Brown & Yang, "Adverse selection, speed bumps and asset market quality" (UEA WP 2015).** Betfair's 5-9 s in-play bet delay against
    "pitch-siders": longer delays cut quoted spreads 7.3%, effective spreads 3.8%, raised depth 19% and order count 87%. **Without a
    delay, liquidity providers are picked off by whoever sees the news first; SIG has no delay, and we are the fast side only if our
    reference is Polymarket and theirs is the TV.**
24. **Caanberry, "Betfair in-play delay explained".** Delays: racing 2 s, football 5-8 s, tennis/cricket 5 s; the delay lets makers
    cancel before a faster taker can match. **The tool a maker uses on a venue without one: pull or widen.**
25. **Whelan, "Agreeing to disagree: the economics of betting exchanges" (UCD WP 2025/22, CEPR DP20633).** 200k Betfair matches: pre-match
    takers -2.5%, makers +0.6%; longshot takers ~-5% at 5% probability; **in the last 15 minutes in-play, takers on longshots lose ~-70%**
    ("Yogi Berra effect") and makers lose on them too. **Late in-play, near-dead longshots are hugely overpriced: sell them.**
26. **Angelini, De Angelis & Singleton, "Informational efficiency and behaviour within in-play prediction markets" (IJF 2022).** Mispricing
    rises after surprising news (a late longshot goal); reverse favourite-longshot bias in-play. With **Choi & Hui (2014, JEBO), "The role
    of surprise"** (abstract only): underreaction to expected goals, overreaction to surprising ones. **A surprise early result (a D hold
    in a lean-R seat) will be overreacted to on the national contracts.**
27. **Ötting, Michels, Langrock & Deutscher, "The reaction to news in live betting" (arXiv 2108.00821).** 1 Hz Bundesliga stakes: bettors
    pile in right after goals; bookmakers suspend after events. Activity falls once outcomes look decided.
28. **Dev.to, "How BTC 5-minute scalpers actually work on Polymarket" (2026).** Bots reconstruct a fair value from a faster feed and IOC-hit
    "stale" Polymarket orders in the final 45-90 s; dynamic size by depth and edge; flatten before resolution. **Stale-order sniping is
    the dominant fast strategy on Polymarket; it is ordinary trading on public information.**

**The last hours before a close**
29. **Restocchi, McGroarty, Gerding & Johnson, "The temporal evolution of mispricing in prediction markets" (FRL 2019; PredictIt
    2014-16).** Favourite-longshot mispricing averages 0.025 but **0.151 in the last 24 h** for markets > 50 days; herding by media-drawn
    unsophisticated bettors; informed traders are time- and liquidity-constrained. (Also used in A3.)
30. **Lee, Mucklow & Ready, "Spreads, depths, and the impact of earnings information" (RFS 1993); Krinsky & Lee, "Earnings announcements
    and the components of the bid-ask spread" (JF 1996).** Specialists **widen spreads and cut depth before** announcements, more so
    before bigger moves; the adverse-selection component rises around the announcement while inventory/processing components fall.
    **Market makers manage pre-announcement risk with depth as much as with price.**
31. **Levi & Zhang (JFE 2015), "Asymmetric decrease in liquidity trading before earnings announcements".** Buyers withdraw before the
    news, sellers needing liquidity keep selling at a discount; the liquidity provider earns the announcement premium. **In the last day,
    the side that keeps trading is the one that must; lean bids only where we want inventory to the outcome.**
32. **Auxiliary:** "Are final market prices sufficient for information aggregation? Last-minute dynamics in parimutuel betting" (arXiv 2509.14645, Japan racing): ~50% of money in the last 5 minutes, and late odds moves
    carry information beyond final odds. Kalshi trading-hours help page; DDHQ pricing page (Votes Free: live results and DDHQ calls;
    Pro $24.99 one-off; Enterprise $5-10k+); AP Elections API overview (calls + counts, polling API, contact elections_api_info@ap.org,
    no public price); Dubach, "Anatomy of a decentralized prediction market" (arXiv 2604.24366, Aug 2026): Polymarket half-spreads
    1,300-1,800 bps in the tails, **depth decays toward resolution** (log-log slope 0.55), ~32 effective makers per market.

## 3. Ideas

### EN-1. Ask SIG three precise questions, not one   [source: rules text; FC-1; Polymarket race rules (#20); our close logic (section 0)]
What the source says: The rules accept trades until 12:00 pm ET 4 Nov; the API says 00:00 UTC. Polymarket resolves per race on three
calls; SIG's rules say nothing about per-market closes, calls, recounts or early payouts.
Idea for us: E-mail (info@thesuper.market): (1) "Will each market's order book accept orders between 00:00 and 17:00 UTC on 4 Nov, or
does trading stop at each market's settlementDate (00:00 UTC)?" (2) "Is trading on publicly reported vote counts and race calls during
that window permitted under the fair-play rules?" (3) "Is a market resolved and paid out as soon as its race is called (does cash return
during the night), and how are races decided when a call is later retracted, a recount or runoff happens, or an independent wins?" Ask
them to confirm whether the `settlementDate` field will change.
Value: decides between case A and case B (case B = the largest P(>= 150k) lever we have); Q3 decides whether cash recycles overnight.
| Cost: one e-mail now; follow up by 20 Oct.
Check: none offline; also watch whether the API's `settlementDate` changes during October.
Fair play: asking is fair; ask in writing so the answer is citable.

### EN-2. Case A (close 00:00 UTC): a staged last-two-hours playbook, defence first   [source: Brown-Yang 2015 (#23), Lee-Mucklow-Ready 1993 / Krinsky-Lee 1996 (#30), Snowberg-Wolfers-Zitzewitz 2004 exits (#1), Restocchi 2019 (#29), Whelan 2025 (#25)]
What the source says: Without a speed bump the slowest quote gets picked off; specialists cut depth and widen before announcements;
exit-poll leaks move markets 25 pts and reverse; the longshot bias is largest in the last 24 h.
Idea for us: (a) **21:00-23:00 UTC**: normal value mode (A3: keep selling rich longshots, never pre-close flatten), but raise `take_edge`
to 0.08 and `take_confirm_seconds` to 120 on the U.S. House/Senate control contracts (exit-poll noise); quotes' size halved; two-way
middle quotes off. (b) **23:00-23:45 UTC**: only value-side quotes at >= the value hurdle, cancel-on-reference-move kept at 3c (the jump
guard exists); KY/IN races: no resting quotes at all (raw votes arrive; faster players watch county results); takes allowed only in KY
races where Polymarket moved >= 5c and has held 2+ minutes (p9 D-12). (c) **23:45** the existing stop window cancels everything:
keep it (15 min is the right size: AP poll-close calls at 00:00 would make any remaining quote stale).
Value: protects ~1-5k of quotes from being picked off (FC-1's estimate); +0.2-0.5k from KY. P(<= 85k) unchanged; P(>= 150k) ~0. | Cost:
3 dated settings changes (a scheduled override or the owner by hand); ~20 lines if we want them keyed to UTC times automatically.
Check: snap03: how many of our resting quotes were within 3c of a Polymarket move in the 2 minutes before our cancel (the stale exposure
per move); the jump-guard log lines.
Fair play: ok (pulling or widening own quotes).

### EN-3. Case A: "two references" in the last hour: the live one for defence, the pre-close one for offence   [source: Snowberg et al. (#1), 538 2018 (#8), Tsang-Yang debate reversal (#11), Fortune 2020 (#2)]
What the source says: The first moves on partial information (exits, the first 5% counted) often reverse (2004 -30 then +65; 2018
538 >80 -> <40 -> ~90; 2020 Trump 80% overnight).
Idea for us: From 22:00 UTC keep the 21:00 UTC Polymarket snapshot. Quotes follow the LIVE price (so we never rest stale), but a take
requires BOTH the live price and the 21:00 snapshot to be take_edge past the tournament quote (i.e. take only gaps that existed before
the noisy hour, or KY races with counted votes). Settlement is at the outcome, so a take on an overreacting reference that later
reverses is a real loss, not a mark.
Value: avoids buying 10-20c tops on exit-poll moves; +0 to +1k. | Cost: ~25 lines (snapshot dict + one extra check in `take_direction`).
Check: replay the 2-3 Oct Polymarket jump moments in snap03: how many takes would the AND rule have cancelled, and their 24 h markouts.
Fair play: ok.

### EN-4. Case B: the election-night mode is mostly REMOVING guards from the existing take path   [source: section 0; Polymarket 2024 (#3, #4); Angelini-De Angelis 2026 (#12); Rothschild-Sethi (#9)]
What the source says: Polymarket reprices within minutes and leads the AP call by hours; thin markets lag 5-15 min (more for obscure
races); stale walls get swept when the reference moves.
Idea for us: A single flag `election_night_mode` (hot toggle) that: (1) overrides the per-market close with 17:00 UTC (or whatever SIG
says) so `stop_minutes_before_close`, `flatten_hours_before_close` and the take window use it; (2) sets `take_tilted_ref` and
`ref_tilt_enabled` off (raw Polymarket); (3) disables the tail guard for takes when the reference is >= 0.97 (or <= 0.03) AND has been
there for >= 10 min (called-race class, EN-6); (4) raises `max_order_cash_frac` to 0.05 and `kelly_max_market_frac` to 0.10 for those
takes; (5) turns off resting two-way quoting on every race whose polls have closed (EN-9); (6) keeps `take_confirm_seconds` 30 for the
called class and 120 + take_edge 0.10 for "moving but not called" races (EN-7). Everything else (book re-download before the take, IOC
TTL 10 s, leftovers cancelled) is the existing path.
Value: the enabler for EN-5..EN-8; on its own nothing. | Cost: ~80-120 lines (close override, class logic, size overrides), all behind
one flag; FC-1 estimated ~150.
Check: live_sim with a fake reference jumping 0.6 -> 0.99 and a fake stale book (by the executor; not run here). Dry-run on the real
API at 23:50 UTC 3 Nov is too late: test the close override against the fake `settlementDate` now.
Fair play: ok: taking existing quotes at prices their owners posted, on public results.

### EN-5. Case B: rank takes by return per dollar of cash, not by cents per share   [source: PredictIt 2020 (#6, #7); Whelan's in-play longshots (#25); our ~0-20k cash]
What the source says: After results, losing sides stay rich (Biden states 88-90c for weeks; Trump +280 EV at 8c); late in-play
longshots lose ~70% for takers.
Idea for us: Each candidate take has return r = (p_ref - price) / price for buying YES, (price - p_ref) / (1 - price) for selling YES
(collateral 1 - price). A called winner offered at 0.90 with p_ref 0.995 returns 10.6% per $ (to the outcome); a loser bid at 0.08 with
p_ref 0.005 returns 8.2% per $; an uncalled race offered at 0.55 with p_ref 0.80 returns 45% per $ but with real risk. Spend scarce cash
(and the 9 takes/min) top-down on r x (1 - residual risk), with a per-race cap (EN-6). The value is in the stale asks on winners and
stale bids on losers that ~1,040 mostly-asleep players leave in the book (their orders don't expire; ours have a 10 s TTL).
Value: with ~20k cash turned over once at ~10-25% average: **+2-5k**; if SIG pays called races out overnight (EN-1 Q3) cash turns over
2-4 times: +5-15k. The cash constraint, not the opportunity, is binding. | Cost: ~30 lines (a priority queue on r over the confirmed takes).
Check: snap03 books at 22:47 3 Oct: total quantity resting on each side more than 5c / 10c / 20c away from Polymarket, and its cash cost:
an upper bound on the night's sweepable stock (if the night's books look like today's).
Fair play: ok.

### EN-6. Case B: size called-race takes off the call, the residual risk and the rule risk, not off Kelly   [source: AP accuracy >99.9%, 2 wrong of 2,500 (#13); Arizona 2020 (#14); Polymarket resolution rule (#20); SIG "may update ... resolution ... at any time" (rules)]
What the source says: AP's error rate is ~0.1%; the risk sits in late-counting states (AZ 2020 shrank to 0.3%); Polymarket resolves
only on three calls.
Idea for us: Three classes. **Certain**: AP called AND Polymarket >= 0.98 for 10+ min (or resolved): residual q = 0.5% (call error +
SIG resolution risk), cap 15% of account per race, 40% total. **Near-certain**: Polymarket >= 0.95 for 30+ min, not yet called:
q = 3%, cap 5% per race. **Moving**: anything else, EN-7. Never treat 0.95 as a call: AZ 2024 touched 95% and fell back (#4).
Exclude races SIG might not resolve cleanly: AK (RCV), ME (RCV), any GA race near 50% (runoff 1 Dec), races with an independent leg.
Value: lets the takes in EN-5 be big enough to matter (Kelly at q 0.5% says ~95% of bankroll; caps keep a wrong call to < 15k).
P(<= 85k): a wrong call on a capped race costs <= 15k; probability ~0.5% per race. | Cost: ~30 lines (class function + caps).
Check: historical: list 2018/2020/2022/2024 statewide AP calls later retracted (none in generals we found; networks' 2000 FL was not
AP's VoteCast-era method).
Fair play: ok.

### EN-7. Case B: uncalled-but-moving races: take only after Polymarket has held its move, and only where our book lags most   [source: Angelini-De Angelis 2026 (#12): drift 5-15 min, bigger in illiquid and less salient markets; Auld-Linton Brexit (#10); 538 2018 / Trump 2020 overshoots (#2, #8); blue/red shift (#16)]
What the source says: Prices underreact on the first minute and drift; but on election nights the reference itself can overshoot on
the order of counting (mirages).
Idea for us: For races not in the certain/near-certain class: take_edge 0.10, confirm 120-300 s, size 2% per race, and **a
mirage filter**: no take toward R in PA/MI/WI before ~04:00 UTC (mail counted late, D shift to come) and none toward D in OH/NC/FL/GA in
the first hour after close (early votes D, erode over ~2 h per AP Ohio notes); none in AZ/NV/CA (late ballots, days). Prefer obscure House
seats (the crowd's underreaction is largest there) only where Polymarket's own market has depth (>= $5k within 2c), else skip.
Value: +1-4k at real risk; the filter mainly avoids the 2020-type 40-point overshoot. | Cost: ~40 lines (state lists by count order,
depth check on the Polymarket book which `ref_prices` does not fetch today: +30 lines for a CLOB `/book` read).
Check: untestable offline (no 2026 night); sanity-check the state lists against AP's 2026 "What to expect" pages when they appear
(late October).
Fair play: ok.

### EN-8. Case B: cash for takes comes from selling what is already near 1 (and the set legs), cheapest forgone return first   [source: Whelan (#25), Levi-Zhang (#31), LIT_REVIEW A4 (sets)]
What the source says: Late, the side that must trade pays; near-dead longshots are overpriced; certain winners trade a few cents below 1.
Idea for us: Sources of cash, in order of the return they forgo, f = (1 - bid) / bid for a YES we hold on a called winner (sell at the
bid) and the same for the winning NO leg of a NO+NO set: (1) sell called-winner YES / NO legs to tournament bids >= 0.98 (forgo <= 2%);
(2) then >= 0.96; (3) then positions in uncalled races whose bid is ABOVE Polymarket (value-mode guard already allows that). Sell only
when the best take in the queue (EN-5) returns more than f + 2%. Never sell into a bid below Polymarket minus 1c on a called race.
Value: converts ~10-30k of near-certain positions into take cash overnight; this is what makes EN-5 scale beyond the ~0-20k we have
(+2-6k on top of EN-5). | Cost: ~40 lines (a "cash recycler" that pairs a sale with the take it funds; reuse the covered-sale path).
Check: snap03: which of our 237 legs have Polymarket >= 0.95 today and what quantity, and the bid depth at >= 0.95 on each (a proxy for
what can be sold that night).
Fair play: ok (selling our own positions at posted bids).

### EN-9. Case B: no resting two-way quotes on any race whose polls have closed; value-side resting orders only far from the reference   [source: Brown-Yang speed bump (#23), Croxson-Reade (#22), Budish et al. latency arbitrage (not fetched; QJE 2015/2022 abstracts from memory: races are fast and the stale quote always loses)]
What the source says: On a venue without a bet delay, the slowest quote in the book pays the latency-arbitrage tax.
Idea for us: From each state's poll close: cancel and stop two-way quoting in its races; keep only resting bids on called winners at
<= p_ref - 2c and asks on called losers at >= p_ref + 2c (fills come from players who want cash or a lottery ticket: Whelan's late
longshot buyers). Keep the jump guard (pull 60 s on a 3c reference move) everywhere else.
Value: avoids being the slow one (-0.5 to -3k avoided); the far value quotes add +0.2-1k. | Cost: ~25 lines (a poll-close time per
race; a dict of state -> UTC close from section 1).
Check: none offline.
Fair play: ok.

### EN-10. Case B: write budget triage for the 01:00-05:00 UTC peak   [source: SIG changelog (30 writes/min/account; dropped live updates); our take = 3 writes]
What the source says: 28 writes/min; each take costs 3; the realtime feed drops updates under load.
Idea for us: In election-night mode: (1) stop all quote refreshes on closed-poll races (EN-9 frees most writes); (2) batch: one write
can carry 10 orders, so put up to 10 take orders on DIFFERENT markets into one write after cancelling our own quotes in one write (2
writes per 10 takes instead of 30); (3) order the queue by EN-5's r; (4) re-download books by REST (100 reads/min) only for the top-20
candidates. That turns ~9 takes/min into ~50-100.
Value: x5-10 throughput in the hour when the stale stock is largest (the first 30-60 min after each wave); worth +1-3k on its own if
cash is available. | Cost: ~60 lines (a batched take; today `place_orders([order])` sends one).
Check: live_sim writes_pm with a burst of 40 confirmed takes (executor).
Fair play: ok.

### EN-11. Use Polymarket as the results feed; Kalshi's on-platform AP calls / DDHQ Free as a confirmation, nothing paid   [source: Polymarket 2024 (#3, #4); Kalshi-AP licence (#5); DDHQ pricing (#32); AP API overview (#32)]
What the source says: Polymarket led AP calls by hours; Kalshi shows AP calls and counts live; DDHQ Free shows live results with DDHQ
calls; AP's API is sold to media (no public price).
Idea for us: Reference = Polymarket at 5 s (already). Add a **resolved-market rule**: if a mapped Polymarket market is `closed` with
`outcomePrices` [1,0], use p = 1 / 0 (class "certain") instead of dropping it as illiquid. Optional confirmation for the "certain"
class: the owner (or a 30-line scraper of a public AP results page, if its terms allow) marks a race called. No AP/DDHQ API purchase:
latency gains of seconds don't matter against a crowd that lags by minutes.
Value: keeps the most valuable races (resolved = p exactly 1) in the take set; avoids paying for feeds. | Cost: ~20 lines in
`ref_prices.py` (closed-market handling) + a manual "called" list setting.
Check: `ref_prices.py check` against a 2025 resolved Polymarket market (VA Governor 2025, resolved) to see what gamma returns.
Fair play: ok (public prices and public calls; respect each site's terms).

### EN-12. Case A or B: do not let the first hour's bellwethers move our national-control positions   [source: 538 2018 (#8), Cohn (#8), Fair ranking (#21), Choi-Hui overreaction to surprise (#26), Axios 2022 VA-07 (#18)]
What the source says: First-hour inferences (KY-06, VA-02/07, Miami-Dade) produced a false 2018 panic and a correct 2022 read; surprise
news is overreacted to.
Idea for us: Treat U.S. House / U.S. Senate control as "moving" class until at least 03:00 UTC (enough Senate seats called), with
take_edge 0.10; but DO quote far value orders on them (a panic seller at 0.70 on Dem House when Polymarket is 0.90 is a gift only if
Polymarket is right; the far order limits the damage if it isn't).
Value: avoids a 2018-type -20-point round trip on our largest position (Dem House +8,876 at ~0.84); +0-1k. | Cost: config (per-market
class override list).
Check: none offline.
Fair play: ok.

### EN-13. If SIG says markets stay open, revise the pre-election plan: hold cash for the night   [source: EN-5/EN-8 arithmetic; Restocchi (#29); A3 reserve]
What the source says: The night's opportunity is cash-bound; the last pre-election day's longshot richness is the best place to raise
cash by selling.
Idea for us: Under case B, the A3 end-game reserve (10-15k) should be **kept for the night**, not released into the last-day tilt; and
on 3 Nov sell the richest longshot-NO / favourite-NO set legs (A4) into the last-day bias to raise a further 10-20k of cash for 01:00-05:00
UTC. Under case A, release the reserve in tranches as A3 says.
Value: case B: +3-8k (cash at 10-25% per night vs ~5% to the outcome before); case A: 0. | Cost: config (reserve) + the A4 build.
Check: snap03: the cash the A4 ladder would raise at today's bids.
Fair play: ok.

### EN-14. A "needle-lite": infer each state's swing from counted votes only as a sanity check on Polymarket, never as a reference   [source: Auld-Linton Brexit model led Betfair by ~113 min (#10); NYT needle (#8); MIT blue shift (#16)]
What the source says: A simple results model beat Betfair by ~2 h on Brexit night; the NYT needle was right in 2018 once its data
flowed; but count-order effects (blue/red shift with rising variance) wreck naive models.
Idea for us: Not worth building in 4 weeks. Instead, during the night the owner watches the NYT needle / DDHQ and can veto a take class
(a manual "pause takes on race X" setting) when the needle and Polymarket disagree by > 15 pts.
Value: insurance against EN-7's mirage risk; small. | Cost: a setting (list of paused races), ~10 lines.
Check: none.
Fair play: ok.

## 4. TOP 5 for our situation
1. **EN-1: ask SIG the three questions today** (orders accepted 00:00-17:00 UTC? trading on public returns allowed? called races paid
   out overnight / how retractions, recounts, runoffs are resolved?). Everything else is conditional on the answer.
2. **EN-4 + EN-6 + EN-11 (case B core):** the close override, raw Polymarket reference, the tail guard lifted only for the "certain"
   class (AP called + Polymarket >= 0.98 for 10 min, or resolved), per-race cap 15% / total 40%, resolved Polymarket markets kept as
   p = 1/0. Without the close override the bot stops itself at 23:45 UTC whatever SIG says.
3. **EN-5 + EN-8 + EN-10 (case B scale):** rank takes by return per $ of cash; fund them by selling called-winner positions and set legs
   bid >= 0.96-0.98; batch 10 takes per write. Cash, not opportunity, is the binding constraint (plausible +5-15k; more if SIG pays
   out called races overnight).
4. **EN-2 + EN-3 (case A defence):** from 21:00 UTC higher take_edge/confirm on control contracts, value-side-only quotes from 23:00,
   no quotes in KY/IN races, the existing 23:45 stop; takes only on gaps both the live and the 21:00 reference agree on.
5. **EN-7 + EN-12 (both cases): don't trade the first hour's swing**: mirage filters by state count order; control contracts in the
   "moving" class until ~03:00 UTC. The 2004, 2018 and 2020 nights each had a 25-40 point reversal.

**Biggest risk in election-night trading:** trading on a reference that is itself overshooting on the order of the count (2004 exits,
2018 538 <40%, 2020 Trump ~80%) with the tail guard and Kelly limits lifted, at outcome settlement where a reversal is a realised loss.
Second: rule risk (SIG voids or disallows post-00:00 trades, or resolves a recount/runoff race unexpectedly) concentrated by the 15% caps.

## 5. Sources I could not access
- Croxson & Reade full text (ResearchGate 429; abstract via RePEc only); Choi & Hui 2014 (ResearchGate 429; finding from memory of the
  abstract).
- Aquilina, Budish & O'Neill / Budish-Cramton-Shim PDFs (robots 429): cited from memory of the abstracts only.
- CNN pieces on 2024 Polymarket election night and on 2020/2024 red mirage (robots.txt).
- Betfair's 2020 election review (page now shows 2026 content); Polymarket Oracle's table of 95%-vs-AP-call times (table not rendered).
- 538's 2018 election-night live blog (redirects to ABC); newscentermaine RCV explainer (403).
- No hourly price data for Kalshi/PredictIt 2022 Senate/House control on election night was found before the search budget ran out
  (the "Fetterman/Oz path" is therefore not quantified here; the AP calls were: PA Senate early 9 Nov ET, AZ 11 Nov, NV 12 Nov
  (Senate control), House control 16 Nov).
