"""HTTP layer of the FetalAlert API.

Routers live under a version prefix from the first endpoint on. The nodes that
will call this API synchronise on their own schedule and may be running an older
build when the server has already moved on, so a version in the path is what
lets the contract evolve without breaking a node that has not been updated yet.
"""
