"""Which approved documents a run can see.

The rule `_latest_context_entry` in `app/routers/pipeline.py` applies to one key at a time, kept here
so `vector_context.py` can apply the same rule to every key at once without importing a router.
A run sees its own entries, then its source run's, up `source_run_id`, at most
`MAX_SOURCE_RUN_HOPS` runs, except for the keys below, which never leave the run that wrote them.
"""

from __future__ import annotations

# A Phase-2 run chains to the Phase-1 run it was started from. Bounded so a `source_run_id` cycle
# cannot spin a request forever.
MAX_SOURCE_RUN_HOPS = 4

# Context keys that stop at the run that wrote them, instead of being inherited down the
# `source_run_id` chain like everything else.
#
# Inheritance is the default because what a sub-service run inherits describes the *client* — its
# brand design, its CRO client settings, its industry — and none of that changes per sub-service.
# These keys are the exception, and the distinction is about *scope*, not about freshness: each one
# describes the *service* a run is for, and a sub-service is a different service.
#
#   `icp`, `offers`,      — the buyer, and the value ladder priced for that buyer. Phase 1 profiles
#   `offer_ladder`          the buyer of the headline service; Phase 2 runs its own ICP stage for the
#                           sub-service, because the person who buys "Meta Ads" is often not the one
#                           who buys "Social Media Marketing". Inherited, the parent's ICP silently
#                           answered six Phase 2 stages whenever the run's own had not been built —
#                           a stage started out of order read the wrong buyer with no sign of it.
#                           `offers` is here as well as `offer_ladder` because an approved stage is
#                           stored under its asset id, and readiness looks a document up under both.
#   `keyword_clusters`    — search demand for the service the run is for. Phase 1's is demand for
#                           "Social Media Marketing"; Phase 2's is demand for "Meta Ads". They are
#                           different keyword universes with different volumes and different
#                           competition, and one is not an approximation of the other.
#   `selected_headlines`  — the topics the operator picked, which are what every later stage in
#                           that leg is written about. Inheriting them would put Phase 2's Meta Ads
#                           blog, lead magnets and SMS sequence under a headline chosen for the
#                           parent service — the exact topic drift the selection gate exists to
#                           stop, reintroduced silently one level down.
#
# So a Phase-2 run that has not built its own gets a 404 and builds one, rather than quietly
# working from the parent's. Everything else still inherits.
PHASE_SCOPED_CONTEXT_KEYS: frozenset[str] = frozenset(
    {"keyword_clusters", "selected_headlines", "icp", "offers", "offer_ladder"}
)
