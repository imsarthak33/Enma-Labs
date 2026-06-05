"""Tax computation primitives — pure Python, no LLM, decimal-precise.

Every module here is deterministic. The LLM is for extraction; the tax
verdict is computed mechanically from the extraction. That separation is
how we keep audit trails defensible: a CA can trace any computed value
back to a function and a row of input.
"""
