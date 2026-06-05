"""LLM-orchestrating agents.

* :mod:`app.agents.classifier` — LLM document-type classification
* :mod:`app.agents.extractor`  — LLM structured field extraction
* :mod:`app.agents.verifier`   — Deterministic Python verification (NO LLM)
* :mod:`app.agents.pipeline`   — Orchestrator chaining the above
* :mod:`app.agents.tax_engine` — Phase 5; tax verdict computation
* :mod:`app.agents.supervisor` — Phase 6; text-command ReAct loop

Hard rule: agents NEVER instantiate ``httpx.AsyncClient`` directly.
LLM calls always go through :mod:`app.services.llm`.
"""
