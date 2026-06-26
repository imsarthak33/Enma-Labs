"""Provider-adapter base — the env-gated, drop-in-key pattern.

Every external paid-API integration (bank-statement email ingest, the GST
Suvidha Provider GSTR-2B pull, WhatsApp, Account Aggregator, …) follows the
same shape so adding one is mechanical and switching it on is keyless code:

* a **Protocol** describing the capability,
* a **real client** that talks to the provider,
* a **Null client** that is a safe no-op,
* a **factory** that returns the real client *only* when the env carries the
  provider's credentials, else the Null client.

Unconfigured ⇒ Null ⇒ the feature is dormant: the cron poll runs but does
nothing, no network call is made, no error is raised. The code ships fully
built; pasting the subscription keys into the env (and redeploying) is the
*only* action needed to activate it — there is no code change at switch-on.

This keeps acquisition adapters out of the critical path until the founder
actually buys the subscription, while guaranteeing the wiring already works.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Provider(Protocol):
    """Common surface: is this adapter live (credentials present)?"""

    @property
    def is_configured(self) -> bool:
        """True when real credentials are present and the adapter will act."""
        ...
