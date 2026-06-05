"""HTML formatting helpers for outbound Telegram messages.

Markdown is BANNED system-wide. Every outbound Telegram message uses HTML
parse mode. Use the helpers in :mod:`app.formatting.telegram_html` to build
messages — never assemble HTML by string concatenation.
"""
