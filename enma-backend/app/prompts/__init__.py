"""Single source of truth for every LLM prompt in the system.

Three modules:

* :mod:`app.prompts.identity` — persona, voice, tone. Imported by both
  agent and supervisor prompts. Phase 6+ also uses this for user-facing
  HTML text.
* :mod:`app.prompts.master_prompt` — the composed system prompts for each
  agent stage. Functions here take small typed inputs and return the
  string handed to the LLM. No prompt strings live anywhere else.
* :mod:`app.prompts.tax_law_library` — Codified tax-law modules
  (Section 16, Section 17(5), RCM rules, ...). Phase 5 expands; Phase 4
  ships only what the classifier and extractor need.

Hard rule
---------
**Every prompt rendered to the LLM passes through a function in this
package.** No inline f-strings of prompts in agent files. Audit tooling
greps for ``messages=[`` outside this package and ``services/llm.py``.
"""
