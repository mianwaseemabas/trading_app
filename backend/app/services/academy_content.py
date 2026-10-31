"""Qivynex Academy curriculum — beginner, step by step, about THIS app.

Each lesson: id, title, minutes, goal, explain (short plain-language idea), steps (things to do, with a link to the page),
check (optional key the app can verify automatically), quiz (2–3 questions: q, options, answer index, why).
"""

L = "link"

COURSES = [
    {
        "id": "start", "title": "1. Start here", "icon": "🚀",
        "summary": "What the app does, how to stay safe, and the five trading modes.",
        "lessons": [
            {
                "id": "start-tour", "title": "What this app does (a 5-minute tour)", "minutes": 5,
                "goal": "Know what each menu item is for, so nothing feels scary.",
                "explain": "Think of the app as a careful assistant. It watches prices, looks for trade ideas with fixed rules, checks every idea "
                           "against your safety limits, and only then places a trade (or asks you first). It also keeps a diary of everything. "
                           "It does NOT know the future — no app does. Its job is to keep you disciplined and to measure what works.",
                "steps": [
                    {"text": "Open the Terminal. This is your cockpit: price chart, watchlist on the left, order ticket on the right.", L: "#/terminal"},
                    {"text": "Open the Dashboard. The coloured badges at the top show the trading mode and whether data is live or simulated.", L: "#/dashboard"},
                    {"text": "Open Alerts. Every important event lands here, and copies go to your Telegram/email.", L: "#/alerts"},
                    {"text": "Open Setups to approve. When the strategy finds a trade idea it waits here for your yes/no (unless you switch on auto-trade).", L: "#/setups"},
                    {"text": "Open Strategy lab & auto-trade. Here you compare strategies and decide which accounts may trade without asking.", L: "#/lab"},
                    {"text": "Glance at Trades. This is your results history — the honest scoreboard.", L: "#/trades"},
                ],
                "quiz": [
                    {"q": "Can the app predict where the price will go?", "options": ["Yes, the AI knows", "No — it follows rules and measures results", "Only on crypto"],
                     "answer": 1, "why": "No tool can know the future. The app's value is discipline, risk control and honest measurement."},
                    {"q": "Where do trade ideas wait for your decision?", "options": ["News", "Setups to approve", "Audit"], "answer": 1,
                     "why": "Setups to approve shows each idea with its reasons, risk in money, and an Approve / Decline button."},
                ],
            },
            {
                "id": "start-secure", "title": "Make your account safe", "minutes": 4, "check": "totp",
                "goal": "Turn on two-factor login so nobody can use your account even if they steal the password.",
                "explain": "This app can place trades. So your login is as important as your bank login. Two-factor authentication (2FA) "
                           "asks for a 6-digit code from your phone each time you sign in.",
                "steps": [
                    {"text": "Install Google Authenticator or Microsoft Authenticator on your phone."},
                    {"text": "Go to My account → Two-factor authentication → scan the QR code with the authenticator app.", L: "#/account"},
                    {"text": "Type the 6-digit code to confirm. Save the backup codes somewhere safe (not in a screenshot on the same phone)."},
                    {"text": "Never share your password, API keys or Telegram bot token with anyone — including 'support' people who message you."},
                ],
                "quiz": [
                    {"q": "Someone on Telegram says they are 'Qivynex support' and asks for your API key. What do you do?",
                     "options": ["Send it, they are support", "Never share it — block them", "Send only half"], "answer": 1,
                     "why": "Your app is private. Nobody needs your keys. Anyone asking is trying to steal."},
                ],
            },
            {
                "id": "start-modes", "title": "The five modes: from OFF to real money", "minutes": 5,
                "goal": "Understand that you climb a ladder: signals → practice → demo → small real money.",
                "explain": "OFF = nothing happens. SIGNALS ONLY = ideas are logged, no trades. PRACTICE (PAPER) = fake money inside the app with real prices. "
                           "BROKER DEMO / CRYPTO TESTNET = fake money but on the real broker or exchange. LIVE = real money, locked until you pass the live gate. "
                           "Professionals spend weeks on practice and demo. Skipping steps is the #1 way beginners lose money.",
                "steps": [
                    {"text": "On the Dashboard, find the mode buttons. Make sure the forex mode is PRACTICE (PAPER).", L: "#/dashboard"},
                    {"text": "On Brokers → Crypto, set the crypto mode to PAPER (practice) for now.", L: "#/brokers"},
                    {"text": "Notice that LIVE buttons are locked. That is on purpose — you unlock them later with evidence (lesson 'The live gate')."},
                ],
                "quiz": [
                    {"q": "Which mode should a beginner start with?", "options": ["LIVE with small money", "PRACTICE (PAPER)", "OFF"], "answer": 1,
                     "why": "Practice uses real prices but fake money, so you learn the buttons and the feelings without losing anything."},
                    {"q": "What is the difference between PAPER and DEMO/TESTNET?", "options": ["None", "Paper is inside the app; demo/testnet sends orders to the real platform's practice account", "Demo uses real money"],
                     "answer": 1, "why": "Demo/testnet proves the connection to the broker/exchange works, still without real money."},
                ],
            },
        ],
    },
    {
        "id": "basics", "title": "2. Trading basics (zero knowledge)", "icon": "📘",
        "summary": "Prices, candles, timeframes, stop-loss, take-profit, position size, forex vs crypto.",
        "lessons": [
            {
                "id": "basics-price", "title": "Price, bid, ask and spread", "minutes": 4,
                "goal": "Know why a trade starts slightly negative.",
                "explain": "Every market has two prices. BID = the price you can sell at. ASK = the price you can buy at. ASK is always a bit higher. "
                           "The gap is the SPREAD — a small fee you pay on every trade. Example: EURUSD bid 1.08500, ask 1.08512 → spread 1.2 pips. "
                           "That is why a new trade shows a tiny loss at first.",
                "steps": [
                    {"text": "In the Terminal, look at the watchlist: each market shows a price. Click EURUSD.", L: "#/terminal"},
                    {"text": "Find the Risk strip above the chart: it shows the spread and liquidity right now."},
                    {"text": "Click BTCUSDT (if crypto is on). Crypto spreads are tiny but exchange fees (≈0.1% per side) are the main cost."},
                ],
                "quiz": [
                    {"q": "You BUY at the…", "options": ["Bid", "Ask", "Spread"], "answer": 1, "why": "Buy at ask, sell at bid. The difference is the spread."},
                    {"q": "Why does a brand-new trade show a small loss?", "options": ["A bug", "The spread / fees", "The broker cheats"], "answer": 1,
                     "why": "You paid the spread (and fees) to enter. Price must move a little in your favour just to break even."},
                ],
            },
            {
                "id": "basics-candles", "title": "Candles, timeframes and the zoom chart", "minutes": 6,
                "goal": "Read a candle and switch between 5-second and daily charts.",
                "explain": "A candle shows 4 prices for a period: Open, High, Low, Close. Green = closed higher than it opened, red = lower. "
                           "The TIMEFRAME is the period of one candle: 5s, 1m, 15m, 1H, 4H, 1D. Short timeframes show lots of noise; "
                           "longer ones show the real direction. Beginners should decide direction on 4H/1H and only time entries on 15m. "
                           "Seconds charts (5s/15s/30s) are for watching a trade you are already in — not for finding trades.",
                "steps": [
                    {"text": "In the Terminal chart, press 1H, then 4H, then 1D. See how the picture gets calmer.", L: "#/terminal"},
                    {"text": "Press 5s. Watch candles being built live from the prices the app receives."},
                    {"text": "Zoom: scroll the mouse wheel on the chart (phone: pinch with two fingers). Move back in time: drag (phone: swipe)."},
                    {"text": "Hover (or tap) a candle to read its Open/High/Low/Close. Press '⇥ Latest' to jump back to now."},
                ],
                "quiz": [
                    {"q": "A green candle means…", "options": ["Price closed above where it opened", "Good time to buy", "High volume"], "answer": 0,
                     "why": "Colour only tells you close vs open for that period. It is not a buy signal."},
                    {"q": "Which timeframe should decide the main direction for a beginner?", "options": ["5 seconds", "1 minute", "4 hours / 1 hour"], "answer": 2,
                     "why": "Higher timeframes filter out noise. Use small ones only for timing."},
                ],
            },
            {
                "id": "basics-sltp", "title": "Stop-loss, take-profit and 'R'", "minutes": 6,
                "goal": "Never enter a trade without knowing exactly where you exit.",
                "explain": "STOP-LOSS (SL) = the price where the trade closes automatically to stop the loss. TAKE-PROFIT (TP) = where it closes in profit. "
                           "'R' is the money you risk. If the stop is hit you lose 1R. If you risk $2 and win $4, that is +2R. "
                           "This app refuses any trade without a stop-loss — that rule alone saves most beginners.",
                "steps": [
                    {"text": "In the Terminal order ticket, choose BUY. Notice the stop-loss field is required.", L: "#/terminal"},
                    {"text": "Type a stop distance (e.g. 15 pips on EURUSD, or 1.5% on BTC). Look at 'P/L if SL' and 'P/L if TP'."},
                    {"text": "Change the take-profit to twice the stop distance. 'R:R' should show about 2."},
                    {"text": "Don't place it yet — just learn to read the numbers before every trade."},
                ],
                "quiz": [
                    {"q": "You risk $5. The trade hits a target worth $10. How many R?", "options": ["+0.5R", "+2R", "+10R"], "answer": 1, "why": "$10 ÷ $5 = 2R."},
                    {"q": "Can you remove the stop-loss 'to give the trade room'?", "options": ["Yes", "No — the app requires a stop and you should never widen it out of hope"],
                     "answer": 1, "why": "Moving or removing stops out of hope is how small losses become account-killers."},
                ],
            },
            {
                "id": "basics-size", "title": "Position size: risk a small % per trade", "minutes": 6,
                "goal": "Size trades so one loss is small and boring.",
                "explain": "Professionals usually risk 0.25%–1% of the account per trade. With $500 and 0.5% risk, one loss = $2.50. "
                           "Even 10 losses in a row = about −5%, which you can recover from. The app calculates the size for you: "
                           "you set the stop, choose 'Risk $', and it picks the lot size / coin amount.",
                "steps": [
                    {"text": "Open Risk & Strategy and read 'Risk per trade' and the daily/weekly loss limits.", L: "#/risk"},
                    {"text": "In the Terminal ticket, pick sizing 'Risk $' and type 2. Change the stop distance — watch the size change, the risk stays $2.", L: "#/terminal"},
                    {"text": "Remember: wider stop → smaller size. Tighter stop → bigger size. The money at risk stays the same."},
                ],
                "quiz": [
                    {"q": "Account $1,000, risk 0.5%. Maximum loss per trade?", "options": ["$0.50", "$5", "$50"], "answer": 1, "why": "0.5% × $1,000 = $5."},
                    {"q": "You lost 3 trades. What should you do with your risk?", "options": ["Double it to win back", "Keep it the same (or lower it)", "Remove the stop"],
                     "answer": 1, "why": "Raising risk after losses (martingale) is the fastest way to blow an account."},
                ],
            },
            {
                "id": "basics-fx-crypto", "title": "Forex vs crypto in this app", "minutes": 5,
                "goal": "Know the key differences before you choose where to practise.",
                "explain": "FOREX: currency pairs (EURUSD), measured in pips and lots, traded through a broker, closed at weekends, leverage available. "
                           "CRYPTO here: coins against USDT (BTCUSDT), measured in coins and %, traded on an exchange, open 24/7. "
                           "This app trades crypto SPOT only — you buy the coin with USDT, no borrowing, so you can only profit when price goes up "
                           "(the strategy only takes BUY setups on crypto). Crypto moves more, so the same % stop gets hit more often.",
                "steps": [
                    {"text": "In the Terminal watchlist, use the Forex and Crypto tabs to switch markets.", L: "#/terminal"},
                    {"text": "On Brokers → Crypto, check which coins you watch (e.g. BTCUSDT, ETHUSDT).", L: "#/brokers"},
                ],
                "quiz": [
                    {"q": "In this app, can you SELL BTC short on the exchange to profit from a fall?", "options": ["Yes", "No — spot only, you can only sell coins you own"],
                     "answer": 1, "why": "Spot trading has no borrowing, so no shorting. That is safer: no liquidations."},
                ],
            },
        ],
    },
    {
        "id": "reading", "title": "3. Reading the market in the app", "icon": "🔍",
        "summary": "Terminal, indicators in plain words, news and headlines, signal scores.",
        "lessons": [
            {
                "id": "read-terminal", "title": "The Terminal, piece by piece", "minutes": 6,
                "goal": "Know what every box in the Terminal tells you.",
                "explain": "Top row: account, balance, P/L. Left: watchlist (★ to favourite). Cards: Highlights, Sentiment, Insights, Achievements. "
                           "Risk strip: risk score, volatility, spread, liquidity. Centre: zoom chart with EMA 20 (blue) and EMA 50 (purple). "
                           "Right: order ticket. Bottom: your open positions.",
                "steps": [
                    {"text": "Open the Terminal and star 3 markets you want to follow.", L: "#/terminal"},
                    {"text": "Read the Insights card for one market. Press the AI brief button (if AI is on) for a plain-English summary."},
                    {"text": "Hover 'ⓘ why' on the risk strip to see how the risk score is calculated."},
                ],
                "quiz": [
                    {"q": "The risk strip says 'High risk (78/100)' and spread is wide. Best action?", "options": ["Trade bigger", "Wait or trade smaller", "Remove the stop"],
                     "answer": 1, "why": "High volatility and wide spreads make stops less reliable. Waiting is a valid decision."},
                ],
            },
            {
                "id": "read-indicators", "title": "EMA, RSI, MACD, ATR — in plain words", "minutes": 7,
                "goal": "Understand the 4 tools the strategies use.",
                "explain": "EMA = average price line. Price above a rising EMA 200 = uptrend. RSI (0–100) = how stretched the move is; >70 stretched up, <30 stretched down. "
                           "MACD histogram = momentum turning (crossing zero). ATR = normal candle size; stops are placed 1.5–2× ATR away so normal noise doesn't hit them. "
                           "No indicator is a crystal ball — they just describe what already happened.",
                "steps": [
                    {"text": "On the Terminal chart (1H), see whether price is above or below the purple EMA 50.", L: "#/terminal"},
                    {"text": "Open Market and pick EURUSD H1: the chart shows the strategy's indicators and BUY/SELL triangles.", L: "#/market"},
                    {"text": "Open Signals and click any row: the reasons list shows exactly which indicator values triggered it.", L: "#/signals"},
                ],
                "quiz": [
                    {"q": "Why are stops placed using ATR?", "options": ["It looks nice", "So normal price noise doesn't hit them", "ATR predicts the target"],
                     "answer": 1, "why": "ATR measures normal movement. A stop smaller than normal noise gets hit for no reason."},
                    {"q": "RSI is 85. What does it tell you?", "options": ["Price will fall now", "The recent move is stretched — not a guarantee of reversal", "Buy more"],
                     "answer": 1, "why": "Stretched markets can stay stretched. RSI describes, it doesn't predict."},
                ],
            },
            {
                "id": "read-news", "title": "News, headlines and the news filter", "minutes": 6,
                "goal": "Avoid getting hit by big economic news.",
                "explain": "Some scheduled releases (interest rates, jobs/NFP, inflation/CPI) can move prices violently in seconds. The economic calendar shows them; "
                           "the NEWS FILTER blocks new trades 30 min before to 15 min after high-impact events. Market headlines (central banks, forex, crypto, business) "
                           "are tagged High/Medium/Low by keywords, and High ones about your markets come to Telegram.",
                "steps": [
                    {"text": "Open News. Check today's red (High) events for USD and EUR.", L: "#/news"},
                    {"text": "Scroll to Market headlines. Tick 'only my markets'. Read one High headline and click it to see the source."},
                    {"text": "In Live trading → controls, confirm the News filter is ON.", L: "#/live"},
                ],
                "quiz": [
                    {"q": "US CPI (inflation) is in 20 minutes. You want to open EURUSD. What happens?", "options": ["Nothing", "The news filter blocks new trades on USD pairs", "The app predicts the CPI"],
                     "answer": 1, "why": "That's the point of the filter: avoid entering right before a known volatility spike."},
                ],
            },
            {
                "id": "read-score", "title": "Signal score, verdict and why 'UNKNOWN' is honest", "minutes": 6,
                "goal": "Read a setup report like a professional.",
                "explain": "Each setup gets a score 0–100 from fixed rules (trend, structure, indicators, net reward:risk after costs, volatility, news/session). "
                           "Score is a QUALITY score, not a probability of winning. The verdict is NO TRADE, WATCH, PAPER TEST or RESEARCH SUPPORTED. "
                           "The win probability says UNKNOWN until at least 30 similar setups have finished — the app refuses to invent numbers.",
                "steps": [
                    {"text": "Open Signals and click a recent signal: read Score, Verdict, gates and the economics box (net R:R, break-even win rate).", L: "#/signals"},
                    {"text": "Open Setups to approve: read 'reasons for' AND 'reasons against' before any decision.", L: "#/setups"},
                ],
                "quiz": [
                    {"q": "Score 82/100 means…", "options": ["82% chance to win", "Good rule quality — not a probability", "Guaranteed profit"], "answer": 1,
                     "why": "The score measures how well the setup meets the rules. Profit chance is only known from real results."},
                ],
            },
        ],
    },
    {
        "id": "practice", "title": "4. Your first practice trades", "icon": "🎯",
        "summary": "Place, manage and review trades with fake money.",
        "lessons": [
            {
                "id": "prac-first", "title": "Place your first practice trade", "minutes": 7, "check": "paper_trade",
                "goal": "Complete one full trade on PRACTICE from start to finish.",
                "explain": "You will place a tiny practice trade so the buttons stop feeling scary. The result doesn't matter — the process does.",
                "steps": [
                    {"text": "Make sure the mode badge at the top says PRACTICE (PAPER).", L: "#/dashboard"},
                    {"text": "Terminal → click EURUSD (or BTCUSDT for crypto practice).", L: "#/terminal"},
                    {"text": "Look at the 1H chart: is price above the purple line (uptrend)? Pick BUY if yes, SELL if no (crypto: BUY only)."},
                    {"text": "Stop distance: 15 pips (forex) or 1.5% (crypto). Take-profit: 2× the stop."},
                    {"text": "Sizing: Risk $ → 2. Read 'P/L if SL' and 'P/L if TP'. Press Place."},
                    {"text": "You get an ORDER FILLED alert. The trade appears in 'Open positions' with dashed entry/SL/TP lines on the chart."},
                ],
                "quiz": [
                    {"q": "What three things must you know BEFORE pressing Place?", "options": ["Entry, stop-loss, money at risk", "Only the entry", "The news headline"],
                     "answer": 0, "why": "Entry, exit if wrong, and the money you can lose. Everything else is secondary."},
                ],
            },
            {
                "id": "prac-manage", "title": "Manage a trade: stops, break-even, journal", "minutes": 6,
                "goal": "Learn the few actions allowed after entry — and what never to do.",
                "explain": "Allowed: move the stop CLOSER to protect profit (break-even at +1R, or a trailing stop), close early if your reason is gone, write a journal note. "
                           "Not allowed: moving the stop FURTHER away, adding to a losing trade, panicking because of 5-second candles.",
                "steps": [
                    {"text": "Open Live trading and click your open position.", L: "#/live"},
                    {"text": "Find auto-protect: break-even at +1R and/or trailing stop. Turn break-even on."},
                    {"text": "Write a 1-line journal note: why you entered and how you felt."},
                    {"text": "Wait for the stop or target. When it closes you get a STOP HIT or TARGET alert."},
                ],
                "quiz": [
                    {"q": "Your trade is at −0.8R and you 'feel' it will turn. What do you do?", "options": ["Move the stop further", "Leave the stop where it is", "Add another trade"],
                     "answer": 1, "why": "The stop was planned when you were calm. Let it do its job."},
                ],
            },
            {
                "id": "prac-approve", "title": "Approve or decline a strategy setup", "minutes": 5,
                "goal": "Make a fast, reasoned decision on the strategy's ideas.",
                "explain": "When the strategy finds a qualified setup, you get a 'Trade opportunity' alert with the timeframe and how long it stays valid. "
                           "Open Setups to approve, read the report, then Approve (the app re-checks price and risk at that moment) or Decline with a reason. "
                           "Declining is a good habit when news is near or the reasons against are strong.",
                "steps": [
                    {"text": "Open Setups to approve. If there is a setup, read hypothesis, invalidation and risk in $.", L: "#/setups"},
                    {"text": "Check News for the two currencies (or the coin) before approving.", L: "#/news"},
                    {"text": "Approve or decline. Write a reason when you decline — it trains your judgement."},
                ],
                "quiz": [
                    {"q": "The setup is valid for 2 hours but you see it 3 hours later. What happens if you approve?", "options": ["It trades anyway", "It has expired / the app re-checks and refuses if price passed the levels"],
                     "answer": 1, "why": "The app re-checks at approval time. Stale setups are not placed."},
                ],
            },
            {
                "id": "prac-review", "title": "Read your results honestly", "minutes": 6,
                "goal": "Judge yourself by expectancy and drawdown, not by one trade.",
                "explain": "After 20+ trades look at: net profit after costs, win rate, average R, profit factor (> 1 means profitable), max drawdown. "
                           "One win or one loss means nothing. A process is good if it is positive over many trades and the drawdown is tolerable.",
                "steps": [
                    {"text": "Open Trades → Performance.", L: "#/trades"},
                    {"text": "Write down: number of trades, win rate, average R, profit factor, max drawdown. Repeat every Sunday."},
                ],
                "quiz": [
                    {"q": "Win rate 40%, average win +2R, average loss −1R. Is that profitable?", "options": ["No, it loses more than it wins", "Yes: 0.4×2 − 0.6×1 = +0.2R per trade"],
                     "answer": 1, "why": "Expectancy = (win% × avg win) − (loss% × avg loss). Positive = profitable before extra costs."},
                ],
            },
        ],
    },
    {
        "id": "strategies", "title": "5. Strategies & testing", "icon": "🧪",
        "summary": "Win rate vs payoff, backtests, the Strategy lab and AI review.",
        "lessons": [
            {
                "id": "strat-winrate", "title": "Why 'high win rate' can still lose money", "minutes": 6,
                "goal": "Understand expectancy — the only number that pays.",
                "explain": "Mean reversion (RSI-2) wins often (55–70%) but small (≈1R or less). Trend following (Donchian) wins less (30–45%) but big (2–2.5R). "
                           "Example A: 65% × 0.8R − 35% × 1R = +0.17R. Example B: 35% × 2.5R − 65% × 1R = +0.23R. Both can work — or both can fail after costs. "
                           "Only testing on YOUR markets with costs tells you which one has an edge right now.",
                "steps": [
                    {"text": "Open Strategy lab & auto-trade and read the Strategy library table: style, typical win rate, payoff, rules.", L: "#/lab"},
                    {"text": "Pick one mean-reversion and one trend strategy to compare later."},
                ],
                "quiz": [
                    {"q": "Strategy wins 70% with avg win +0.3R and avg loss −1R. Expectancy?", "options": ["+0.21R", "−0.09R", "+0.70R"], "answer": 1,
                     "why": "0.7 × 0.3 − 0.3 × 1 = 0.21 − 0.30 = −0.09R. High win rate, still losing."},
                ],
            },
            {
                "id": "strat-backtest", "title": "Run a backtest", "minutes": 7, "check": "backtest",
                "goal": "Test one strategy on past prices with costs.",
                "explain": "A backtest replays history. The app splits data 60% development / 20% validation / 20% FINAL TEST. The final test is the honest part "
                           "because nothing was tuned on it. Fewer than ~30 trades = not enough evidence.",
                "steps": [
                    {"text": "Open Backtest. Choose EURUSD, H1, and run.", L: "#/backtest"},
                    {"text": "Read the warnings first, then the final-test numbers: trades, profit factor, net profit, max drawdown."},
                ],
                "quiz": [
                    {"q": "Which part of a backtest do you trust most?", "options": ["The development part", "The final test (untouched data)", "The best month"], "answer": 1,
                     "why": "Results on data the strategy was never tuned on are the closest thing to the future."},
                ],
            },
            {
                "id": "strat-lab", "title": "Run the Strategy lab and read the ranking", "minutes": 8, "check": "lab",
                "goal": "Let the app test every strategy on every market you watch, then pick candidates.",
                "explain": "The lab runs all strategies × your markets × timeframes with spread, fees and slippage. It labels each result: "
                           "PROMISING (paper test it), TOO FEW TRADES (no conclusion) or NO EDGE SHOWN. Most results will be 'no edge' — that's normal and honest.",
                "steps": [
                    {"text": "Strategy lab → tick H1 and H4 → crypto history 180 days → Run strategy lab.", L: "#/lab"},
                    {"text": "Wait for 'Ranking'. Sort mentally by the Final test column, not total profit."},
                    {"text": "Write down the top 1–2 PROMISING rows (market, timeframe, strategy)."},
                ],
                "quiz": [
                    {"q": "A row shows 90% win rate with 6 trades. Verdict?", "options": ["Use it with real money", "Too few trades — no conclusion"], "answer": 1,
                     "why": "6 trades is luck-sized. You need dozens of trades before trusting any number."},
                ],
            },
            {
                "id": "strat-ai", "title": "Ask the AI to review, then apply (paper first)", "minutes": 5,
                "goal": "Use Claude as a second pair of eyes — you stay the decision maker.",
                "explain": "'Ask AI to review' sends the ranking to Claude. It answers in OBSERVED / INTERPRETATION / UNCERTAINTY / RECOMMENDATION. "
                           "It can't trade and it will say 'none' if nothing shows an edge. Then press 'Use' (or the Crypto/Forex buttons) to make that "
                           "strategy active — it runs on practice/demo first.",
                "steps": [
                    {"text": "Strategy lab → Ranking → Ask AI to review. Read the UNCERTAINTY part twice.", L: "#/lab"},
                    {"text": "Press Use on one PROMISING row. Check 'crypto now' / 'forex now' badges in the library table."},
                    {"text": "Paper-test it for 2–4 weeks before anything else."},
                ],
                "quiz": [
                    {"q": "The AI recommends a strategy. What's the next step?", "options": ["Go live immediately", "Paper/demo test it and measure"], "answer": 1,
                     "why": "AI reviews past numbers. Only forward testing proves the edge still exists."},
                ],
            },
        ],
    },
    {
        "id": "automation", "title": "6. Alerts & auto-trade", "icon": "⚡",
        "summary": "Telegram/email alerts, auto-trade per account, risk limits and the kill switch.",
        "lessons": [
            {
                "id": "auto-alerts", "title": "Telegram & email alerts", "minutes": 5, "check": "telegram",
                "goal": "Receive every important event on your phone.",
                "explain": "Alerts always appear in the app. You choose which are copied to Telegram/email: trade opportunities (with timeframe), "
                           "auto trades, fills, stops/targets, market news, data problems.",
                "steps": [
                    {"text": "Alerts → Where alerts are sent. Tick Telegram for SIGNAL QUALIFIED, AUTO TRADE, ORDER FILLED, STOP HIT, MARKET NEWS. Save.", L: "#/alerts"},
                    {"text": "Press Send test and check your phone."},
                ],
                "quiz": [
                    {"q": "Can a Telegram alert place a trade?", "options": ["Yes", "No — alerts only inform you"], "answer": 1,
                     "why": "Orders come only from you, or from the auto-trade switches you turned on."},
                ],
            },
            {
                "id": "auto-switch", "title": "Turn on auto-trade (practice/testnet first)", "minutes": 6, "check": "auto_on",
                "goal": "Let the app place qualified trades by itself on a practice account.",
                "explain": "Each account has its own switch. When ON, a QUALIFIED setup with score ≥ your minimum and a good enough verdict is placed "
                           "automatically and you get an AUTO TRADE alert. The risk engine still checks every order. Real-money switches stay locked until "
                           "the live gate is approved, and need your password.",
                "steps": [
                    {"text": "Strategy lab & auto-trade → turn ON 'Crypto practice' (or 'Practice (forex paper)').", L: "#/lab"},
                    {"text": "Keep minimum score 70 and verdict PAPER TEST to start."},
                    {"text": "Let it run for 2–4 weeks. Read each AUTO TRADE alert and review results every Sunday."},
                ],
                "quiz": [
                    {"q": "Auto-trade is ON. Your daily loss limit is reached. What happens to the next setup?", "options": ["It trades anyway", "The risk engine blocks it"],
                     "answer": 1, "why": "Auto-trade skips your approval, never the risk limits."},
                ],
            },
            {
                "id": "auto-risk", "title": "Risk limits and the kill switch", "minutes": 5,
                "goal": "Know your emergency brake.",
                "explain": "Limits: risk per trade, daily loss, weekly loss, max open positions, max trades per day. The KILL SWITCH blocks all new orders "
                           "instantly (optionally closing positions). Use it whenever something looks wrong — investigate after.",
                "steps": [
                    {"text": "Risk & Strategy → 'Apply pilot limits' for safe beginner limits (0.25% per trade, 1% per day). Note: this also switches auto-trade off — turn it back on in Strategy lab afterwards.", L: "#/risk"},
                    {"text": "Live trading → find the red KILL SWITCH. Know where it is. (No need to press it now.)", L: "#/live"},
                ],
                "quiz": [
                    {"q": "Prices look broken / a strange trade appears. First action?", "options": ["Wait and see", "Kill switch, then investigate"], "answer": 1,
                     "why": "Stop the damage first. You can always release the kill switch later."},
                ],
            },
        ],
    },
    {
        "id": "platforms", "title": "7. Choosing & connecting a platform", "icon": "🔌",
        "summary": "How to pick a broker/exchange, and connect safely with demo/testnet keys.",
        "lessons": [
            {
                "id": "plat-choose", "title": "How to choose a broker or exchange", "minutes": 8,
                "goal": "Pick a platform that is legal, regulated and testable for you.",
                "explain": "Checklist: (1) Is it allowed to serve residents of your country? (2) Who regulates it (e.g. FCA, CySEC, ASIC; in Saudi Arabia the CMA licenses local firms)? "
                           "(3) Does it have a free demo/testnet? (4) Fees: spread, commission, swap, withdrawal. (5) Does this app support it? "
                           "Supported here — forex: MT5 brokers (via the bridge), cTrader, Capital.com; crypto: Binance, OKX, Bybit (spot). "
                           "Start on the demo/testnet of the one you choose; only fund it after weeks of demo results.",
                "steps": [
                    {"text": "Open Brokers and look at the supported connectors.", L: "#/brokers"},
                    {"text": "For each candidate, check its website: country availability, regulator licence number, demo account, fees. Write them in a note."},
                    {"text": "Ask the AI tutor: 'compare the fees and checks I should do for X vs Y' — then verify on the official sites."},
                ],
                "quiz": [
                    {"q": "Most important first check for a broker?", "options": ["Bonus size", "Regulation and whether it may serve your country", "Nice app colours"], "answer": 1,
                     "why": "An unregulated broker can refuse withdrawals. Bonuses are often traps with conditions."},
                ],
            },
            {
                "id": "plat-crypto", "title": "Connect a crypto exchange (testnet first)", "minutes": 8, "check": "crypto_keys",
                "goal": "Create API keys safely and connect them.",
                "explain": "API keys let the app trade for you. Create them on the exchange's TESTNET first (Binance: testnet.binance.vision). "
                           "Give only 'read' + 'spot trading' permission — NEVER withdrawal. Restrict the key to your server's IP address (37.60.252.21) if the exchange allows it.",
                "steps": [
                    {"text": "On the exchange testnet, create an API key with spot trading ON and withdrawals OFF; add IP restriction 37.60.252.21."},
                    {"text": "Brokers → Crypto → choose the exchange → Add API keys → paste key + secret (+ passphrase for OKX).", L: "#/brokers"},
                    {"text": "Press Test connection. You should see testnet balances. Set crypto mode to TEST."},
                ],
                "quiz": [
                    {"q": "Which permission must NEVER be enabled on the app's API key?", "options": ["Read", "Spot trading", "Withdrawal"], "answer": 2,
                     "why": "With withdrawal off, even a stolen key cannot take your coins out."},
                ],
            },
            {
                "id": "plat-forex", "title": "Connect a forex broker demo", "minutes": 8, "check": "broker",
                "goal": "Get real broker prices and demo orders flowing.",
                "explain": "Option A: Capital.com demo (API key in Brokers). Option B: cTrader demo. Option C: any MT5 broker through the Windows bridge. "
                           "After connecting, switch System → data source to broker data so charts and signals use the broker's real prices.",
                "steps": [
                    {"text": "Create a DEMO account at your chosen broker.", },
                    {"text": "Brokers → choose the connector → enter demo credentials → Use this broker.", L: "#/brokers"},
                    {"text": "System → Use broker data. Then Dashboard → BROKER DEMO.", L: "#/settings"},
                ],
                "quiz": [
                    {"q": "After connecting, how long should you stay on the broker demo?", "options": ["1 day", "2–4 weeks with a written review", "No need"], "answer": 1,
                     "why": "You need enough trades to compare the app with the broker's own records and spot problems."},
                ],
            },
        ],
    },
    {
        "id": "live", "title": "8. Going live safely", "icon": "🛡",
        "summary": "The live gate, your first real-money weeks, routine and psychology.",
        "lessons": [
            {
                "id": "live-gate", "title": "The live gate: evidence before real money", "minutes": 6, "check": "live_gate",
                "goal": "Unlock real money only with proof.",
                "explain": "The live gate is a checklist: enough practice/demo trades, positive results after costs, safety tests passed, alerts working, "
                           "2FA on — and your explicit approval. It expires after 30 days so you re-check regularly.",
                "steps": [
                    {"text": "Open Risk & Strategy → Live gate. Read every item and which ones are still red.", L: "#/risk"},
                    {"text": "Don't try to rush the red items. Each one protects your money."},
                ],
                "quiz": [
                    {"q": "Why does the gate expire after 30 days?", "options": ["To annoy you", "Markets and results change — re-check the evidence"], "answer": 1,
                     "why": "A strategy that worked last month may not work now."},
                ],
            },
            {
                "id": "live-first-week", "title": "Your first real-money weeks", "minutes": 6,
                "goal": "Start so small that mistakes are cheap lessons.",
                "explain": "Use money you can afford to lose completely. Use the pilot limits (0.25% per trade). Trade one market, one strategy. "
                           "Compare every real fill with the demo results. If live results are much worse than demo, stop and find out why.",
                "steps": [
                    {"text": "Fund only a small amount. Keep risk at the pilot limits.", L: "#/risk"},
                    {"text": "Turn on real-money auto-trade ONLY after a few manual live trades went exactly as expected.", L: "#/lab"},
                    {"text": "Every evening: check positions, alerts and the audit log.", L: "#/audit"},
                ],
                "quiz": [
                    {"q": "Live results are much worse than demo for 2 weeks. What now?", "options": ["Increase size", "Stop, compare fills/spreads/fees, find the cause"], "answer": 1,
                     "why": "Differences usually come from costs, slippage or timing — fix the cause before continuing."},
                ],
            },
            {
                "id": "live-routine", "title": "Daily routine & trading psychology", "minutes": 6,
                "goal": "Build habits that keep you in the game.",
                "explain": "5-minute daily routine: News → Alerts → Live trading (every position has a stop) → Dashboard badges green. Weekly: Trades → Performance. "
                           "Rules: no revenge trading, no raising risk after losses, no trading when tired or angry, the kill switch is your friend.",
                "steps": [
                    {"text": "Morning: News → today's High events.", L: "#/news"},
                    {"text": "Midday: Alerts → anything unusual?", L: "#/alerts"},
                    {"text": "Sunday: Trades → Performance → write 3 lines: what worked, what didn't, one change.", L: "#/trades"},
                ],
                "quiz": [
                    {"q": "You just lost 3 trades and feel angry. Best action?", "options": ["Trade bigger to win it back", "Stop for the day"], "answer": 1,
                     "why": "Emotions make decisions worse. The market will be there tomorrow."},
                ],
            },
        ],
    },
    {
        "id": "ai", "title": "9. Getting the most from Claude AI", "icon": "🤖",
        "summary": "Good questions, what AI can and cannot do, and daily AI habits.",
        "lessons": [
            {
                "id": "ai-use", "title": "How to ask the AI good questions", "minutes": 5, "check": "ai_on",
                "goal": "Turn the AI into your patient teacher and second opinion.",
                "explain": "The AI sees the app's data (positions, signals, settings) and explains it. Good questions are specific: "
                           "'Explain signal #123 like I'm new', 'What are the reasons against this setup?', 'Review my last 20 trades — what pattern do you see?'. "
                           "It cannot place trades, cannot see the future, and can be wrong — always verify important facts.",
                "steps": [
                    {"text": "Open AI Assistant and ask: 'Explain my current settings in simple words.'", L: "#/assistant"},
                    {"text": "In the Academy, use 'Ask the tutor' on any lesson you don't fully understand."},
                    {"text": "Ask in Urdu if you prefer — the AI replies in your language."},
                ],
                "quiz": [
                    {"q": "Which is the best question?", "options": ["Will BTC go up tomorrow?", "What are the reasons for and against this BTCUSDT setup, using the app's data?"],
                     "answer": 1, "why": "The AI is great at explaining evidence, not at predicting the future."},
                ],
            },
        ],
    },
]


def all_lessons():
    for c in COURSES:
        for l in c["lessons"]:
            yield c, l


def lesson(lid: str):
    for c, l in all_lessons():
        if l["id"] == lid:
            return c, l
    return None, None
