"""Shared foundation of the OPEN TERMINAL gateway (REBUILD_SPEC §3).

    core.errors    typed errors + the JSON error envelope (install(app) registers handlers)
    core.http      get_json / get_text / get_bytes on urllib (browser UA, proxy env, secret scrubbing)
    core.cache     cached(key, ttl, fn, stale_ttl=…): TTL, single-flight, serve-stale-on-error
    core.status    per-source board surfaced by /api/status
    core.universe  the 23 instruments, their capabilities and source chains
    core.session   open / closed / unknown per market calendar
    core.quotes    QuoteService: source chains, one poller, change vs previous close
    core.candles   normalised OHLCV for /api/candles
    core.fanout    /ws fan-out: topics, throttles, hub-event translation
"""
VERSION = "2.0.0"
