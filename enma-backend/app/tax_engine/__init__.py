"""Tax engine — GST rate data, Income Tax TDS transition, and query interfaces.

This package is separate from ``app.tax`` (which contains the pure-math
engine modules: blocked credits, RCM, GST-TDS, ITC, reconciler).
``app.tax_engine`` holds the **data-first** knowledge modules where
every rate, threshold, and section reference is stored as versionable
data (with ``effective_from`` / ``effective_to``), never hardcoded
application logic.
"""
