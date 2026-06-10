-- 001_backend_schema_snapshot.sql
-- Baseline schema snapshot: all production durable tables.
-- Uses CREATE TABLE IF NOT EXISTS for idempotency.

-- Source outcome history (single-row KV store)
CREATE TABLE IF NOT EXISTS source_outcomes (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Event cache for cold-start recovery
CREATE TABLE IF NOT EXISTS events_cache (
    event_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    cached_at TEXT DEFAULT (datetime('now'))
);

-- Predictions / backtest records
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    event_headline TEXT,
    published_at TEXT NOT NULL,
    ticker TEXT NOT NULL,
    predicted_direction TEXT NOT NULL,
    predicted_score REAL NOT NULL,
    significance REAL,
    confidence REAL,
    relationship_type TEXT,
    categories TEXT,
    source_type TEXT,
    event_stage TEXT,
    price_at_event REAL,
    price_after_1h REAL,
    price_after_4h REAL,
    price_after_24h REAL,
    actual_return_1h REAL,
    actual_return_4h REAL,
    actual_return_24h REAL,
    actual_direction TEXT,
    is_correct INTEGER,
    outcome_status TEXT DEFAULT 'pending',
    resolved_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    market_context_factor REAL,
    volume_signal REAL,
    source_type_count INTEGER,
    rsi_value REAL,
    rsi_factor REAL,
    macd_histogram REAL,
    macd_factor REAL,
    sma_trend TEXT,
    trend_factor REAL,
    event_cluster_count INTEGER,
    event_cluster_factor REAL,
    atr_value REAL,
    atr_pct REAL,
    atr_factor REAL,
    sector_correlation_count INTEGER,
    sector_correlation_factor REAL,
    foreign_market_factor REAL,
    sentiment_momentum TEXT,
    sentiment_momentum_factor REAL,
    currency_factor REAL,
    prediction_origin TEXT DEFAULT 'live',
    source_article_id TEXT,
    time_horizon TEXT DEFAULT '7d',
    signal_tier TEXT DEFAULT 'D',
    signal_type TEXT DEFAULT 'event',
    event_score REAL DEFAULT 0,
    tech_score REAL DEFAULT 0,
    tech_confirmation_count INTEGER DEFAULT 0,
    return_7d REAL,
    return_30d REAL,
    outcome_7d TEXT,
    outcome_30d TEXT,
    UNIQUE(event_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_predictions_status
    ON predictions(outcome_status);
CREATE INDEX IF NOT EXISTS idx_predictions_ticker
    ON predictions(ticker);
CREATE INDEX IF NOT EXISTS idx_predictions_published
    ON predictions(published_at);

-- Historical events (backfill archive)
CREATE TABLE IF NOT EXISTS historical_events (
    article_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT,
    headline TEXT NOT NULL,
    summary TEXT,
    published_at TEXT NOT NULL,
    timestamp_confidence REAL NOT NULL,
    provenance_json TEXT NOT NULL,
    import_status TEXT NOT NULL DEFAULT 'accepted',
    rejection_reason TEXT,
    raw_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_historical_events_published
    ON historical_events(published_at);
CREATE INDEX IF NOT EXISTS idx_historical_events_source
    ON historical_events(source);

-- Source accuracy tracking
CREATE TABLE IF NOT EXISTS source_accuracy (
    source_id TEXT PRIMARY KEY,
    total_predictions INTEGER DEFAULT 0,
    correct_predictions INTEGER DEFAULT 0,
    hit_rate REAL DEFAULT 0.5,
    calibration_multiplier REAL DEFAULT 1.0,
    last_updated TEXT
);

-- Trade signal history
CREATE TABLE IF NOT EXISTS signal_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('BUY', 'SELL')),
    signal_strength REAL NOT NULL,
    price_at_signal REAL NOT NULL,
    stop_loss REAL,
    take_profit REAL,
    risk_reward REAL,
    timeframe TEXT,
    reasons_json TEXT,
    event_headline TEXT,
    event_source TEXT,
    signal_source TEXT DEFAULT 'auto',
    price_after_24h REAL,
    price_after_72h REAL,
    actual_return_24h REAL,
    actual_return_72h REAL,
    outcome TEXT DEFAULT 'pending' CHECK (outcome IN ('pending', 'win', 'loss', 'expired')),
    resolved_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    time_horizon TEXT DEFAULT '7d',
    signal_tier TEXT DEFAULT 'D',
    signal_type TEXT DEFAULT 'event',
    event_score REAL DEFAULT 0,
    tech_score REAL DEFAULT 0,
    tech_confirmation_count INTEGER DEFAULT 0,
    calibration_multiplier REAL DEFAULT 1.0,
    invalidation_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_signal_history_ticker
    ON signal_history(ticker);
CREATE INDEX IF NOT EXISTS idx_signal_history_outcome
    ON signal_history(outcome);
CREATE INDEX IF NOT EXISTS idx_signal_history_created
    ON signal_history(created_at);
CREATE INDEX IF NOT EXISTS idx_signal_history_action
    ON signal_history(action);

-- Portfolio positions
CREATE TABLE IF NOT EXISTS portfolio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('long', 'short')),
    entry_price REAL NOT NULL,
    shares INTEGER NOT NULL DEFAULT 0,
    stop_loss REAL,
    take_profit REAL,
    signal_id INTEGER REFERENCES signal_history(id),
    sector TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    exit_price REAL,
    exit_date TEXT,
    pnl REAL,
    pnl_pct REAL,
    entry_date TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_portfolio_status
    ON portfolio(status);
CREATE INDEX IF NOT EXISTS idx_portfolio_ticker
    ON portfolio(ticker);

-- Pinned tickers
CREATE TABLE IF NOT EXISTS pinned_tickers (
    ticker TEXT PRIMARY KEY,
    pinned_at TEXT DEFAULT (datetime('now'))
);

-- Daily signal snapshots
CREATE TABLE IF NOT EXISTS daily_signal_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    action TEXT NOT NULL,
    time_horizon TEXT NOT NULL,
    signal_tier TEXT NOT NULL,
    entry_price REAL,
    stop_loss REAL,
    take_profit REAL,
    signal_strength REAL,
    reason_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_daily_snapshots_date
    ON daily_signal_snapshots(snapshot_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_snapshots_date_ticker
    ON daily_signal_snapshots(snapshot_date, ticker);

-- Alert deduplication
CREATE TABLE IF NOT EXISTS alert_dedup (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    action TEXT NOT NULL,
    sent_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_alert_dedup_ticker_action
    ON alert_dedup(ticker, action);
