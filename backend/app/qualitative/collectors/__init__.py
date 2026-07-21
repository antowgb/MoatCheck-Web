"""Source collectors for the qualitative layer.

One module per source. Each exposes ``collect(ticker, stock) ->
list[CollectedItem]`` and records its own ``feed_status`` after every run.
All four are fully implemented; activation is gated by
``app.qualitative.config.SOURCE_FLAGS`` (only EDGAR on by default).
"""
