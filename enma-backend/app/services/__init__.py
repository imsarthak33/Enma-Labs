"""External service clients.

Every outbound network call from the backend goes through a module in this
package — there are no ad-hoc ``httpx.AsyncClient`` instantiations in
agents, routes, or queries. Centralising lets us share connection pools,
apply timeouts uniformly, and route every request through Sentry.
"""
