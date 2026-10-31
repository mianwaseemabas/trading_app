// Guided walkthroughs: each step opens a page and points at a real element.
export const TOURS = {
  terminal: { title: "The Terminal", steps: [
    { route: "terminal", sel: ".wl", title: "Your markets", text: "Tap a market to open it. ★ marks favourites. Tabs filter forex, crypto and stocks." },
    { route: "terminal", sel: ".tf-row", title: "Timeframes", text: "5s to 1D. Decide direction on 4H/1H, time entries on 15m. Seconds are for watching a trade." },
    { route: "terminal", sel: ".zchart", title: "The chart", text: "Scroll or pinch to zoom, drag or swipe to move, ⇥ Latest to jump back to now." },
    { route: "terminal", sel: ".chart-tools", title: "Draw & indicators", text: "Draw lines and trend lines, or a 🔔 price alert line. Switch RSI, MACD, Bollinger and volume on and off." },
    { route: "terminal", sel: "[data-tour=explain]", title: "Ask the chart", text: "Claude explains what the chart is doing in plain words — with reasons up AND down." },
    { route: "terminal", sel: ".order-panel", title: "Order ticket", text: "Buy/Sell, stop-loss (required), size from the money you accept to lose. Read 'P/L if SL' before every trade." },
  ] },
  alerts: { title: "Alerts & phone", steps: [
    { route: "alerts", sel: ".alert-list", title: "Inbox", text: "Every event lands here first. Tap one to see details and where it was delivered." },
    { route: "alerts", sel: ".tbl-wrap", title: "Where alerts go", text: "Tick Telegram, Email or Phone app for each alert type, then Save." },
    { route: "alerts", sel: ".phone-app", title: "Phone app", text: "Install the app on your phone and turn on notifications — no Telegram needed." },
    { route: "alerts", sel: ".cmd-grid", title: "Telegram bot", text: "Control the app from Telegram. /status, /positions… and /approve or /kill with your PIN." },
  ] },
  lab: { title: "Strategy lab & auto-trade", steps: [
    { route: "lab", sel: ".auto-grid", title: "Auto-trade switches", text: "One switch per account. Practice and testnet first. Real money stays locked behind the live gate." },
    { route: "lab", sel: "text:Strategy race", title: "Strategy race", text: "All strategies paper-trade side by side. Watch for 30+ trades before trusting one." },
    { route: "lab", sel: "text:Strategy library", title: "Library", text: "What each strategy does, its typical win rate and payoff. Remember: expectancy matters, not win rate." },
    { route: "lab", sel: "text:Test every strategy", title: "Run the lab", text: "Backtests every strategy on your markets with costs. Trust the final-test column." },
  ] },
  start: { title: "Welcome tour", steps: [
    { route: "academy", sel: ".academy-hero", title: "Academy", text: "Short lessons, one step at a time. Your streak and setup checklist are here." },
    { route: "terminal", sel: ".zchart", title: "Terminal", text: "Your cockpit: chart, watchlist and order ticket." },
    { route: "charts", sel: ".multi-grid", title: "Multi-chart", text: "The same market on several timeframes at once." },
    { route: "alerts", sel: ".alert-list", title: "Alerts", text: "Everything important — also sent to Telegram, email or your phone." },
    { route: "lab", sel: ".auto-grid", title: "Auto-trade", text: "Decide which practice accounts may trade without asking you." },
    { route: "tools", sel: ".grid.two", title: "Calculator & simulator", text: "Size trades correctly and see what a strategy's numbers mean over 50 trades." },
  ] },
};

// lesson id -> tour that shows it in the app
export const LESSON_TOUR = { "start-tour": "start", "read-terminal": "terminal", "basics-candles": "terminal", "prac-first": "terminal",
  "auto-alerts": "alerts", "auto-switch": "lab", "strat-lab": "lab", "strat-winrate": "lab", "pow-phone": "alerts", "pow-bot": "alerts",
  "pow-lines": "terminal", "pow-tools": "start" };

export const startTour = (id) => window.dispatchEvent(new CustomEvent("q-tour", { detail: id }));
