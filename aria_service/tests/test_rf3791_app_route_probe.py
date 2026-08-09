"""R-F3791 — the route enumeration must see real routes, and must still MISS absent ones.

The bug being fixed is a guard that went blind: `app.routes` stopped yielding the
routes that `include_router` mounted, so five "is this endpoint registered?" tests
reported a healthy app as having no endpoints at all.

The dangerous fix is the one that makes those five tests green by making the
enumeration return everything, or by comparing against something that cannot fail.
So the contract has TWO halves and both are asserted here: the probe finds what IS
mounted (1-4), and it does NOT find what is not (5-6). A probe that only satisfies
the first half is a guard that has been deleted.
"""
from __future__ import annotations

from fastapi import APIRouter, FastAPI

from ._app_probe import iter_routes, mounted_paths, route_endpoints


def _app_with_router() -> FastAPI:
    app = FastAPI()
    router = APIRouter(prefix="/api/thing")

    @router.get("/one")
    async def one_ep():  # noqa: D401
        return {}

    @router.post("/two")
    async def two_ep():
        return {}

    app.include_router(router)
    return app


def test_a_route_behind_include_router_is_visible():
    """The exact case that broke: routes reachable only through include_router."""
    paths = mounted_paths(_app_with_router())
    assert "/api/thing/one" in paths
    assert "/api/thing/two" in paths


def test_an_include_router_prefix_is_applied():
    """A prefix passed to include_router lives on the include context, not on the
    child's own `.path` — dropping it would report a path no consumer can call."""
    app = FastAPI()
    router = APIRouter()

    @router.get("/leaf")
    async def leaf_ep():
        return {}

    app.include_router(router, prefix="/mounted")
    assert "/mounted/leaf" in mounted_paths(app)


def test_method_and_endpoint_are_recovered():
    keyed = route_endpoints(_app_with_router())
    assert keyed[("GET", "/api/thing/one")] == "one_ep"
    assert keyed[("POST", "/api/thing/two")] == "two_ep"


def test_first_registration_wins_for_a_duplicated_path():
    """R-F2278 depends on this: the FIRST handler registered is the one served, so
    the map must not let a later duplicate overwrite it."""
    app = FastAPI()
    first, second = APIRouter(), APIRouter()

    @first.get("/dup")
    async def canonical_ep():
        return {}

    @second.get("/dup")
    async def shadowed_ep():
        return {}

    app.include_router(first)
    app.include_router(second)
    assert route_endpoints(app)[("GET", "/dup")] == "canonical_ep"


def test_an_unmounted_route_is_still_absent():
    """The half that keeps this a guard. If the probe reported routes that were
    never mounted, every caller would go green and detect nothing."""
    paths = mounted_paths(_app_with_router())
    assert "/api/thing/three" not in paths
    assert not any(p.endswith("/never-registered") for p in paths)


def test_a_router_that_is_never_included_is_invisible():
    """Declaring a route is not mounting it — the probe must reflect the app, not
    the source. This is the failure mode the five converted tests exist to catch."""
    app = FastAPI()
    orphan = APIRouter(prefix="/orphan")

    @orphan.get("/unreachable")
    async def unreachable_ep():
        return {}

    # deliberately NOT included
    assert "/orphan/unreachable" not in mounted_paths(app)


def test_the_live_app_is_actually_enumerable():
    """Capability: the real app, not a fixture. Asserts the concrete routes whose
    invisibility produced the five failures — one per mounted router."""
    from aria_service.main import app

    paths = mounted_paths(app)
    assert "/api/aria/brain/signal" in paths          # aria_router (R-F887)
    assert "/api/aria/vetting/cases" in paths         # vetting_router (R-F3270)
    assert "/health/live" in paths                    # declared on the app directly
    assert len(paths) > 500, f"expected the full table, got {len(paths)}"

    assert route_endpoints(app)[("GET", "/api/aria/health/perf")] == "health_perf_ep"


def test_the_same_router_included_twice_is_enumerated_twice():
    """R-F3792's cycle guard must track the ANCESTOR CHAIN, not every container it
    has visited. Including one router twice is a legitimate way to create a
    duplicate route, so a visited-set would blind the duplicate detector to exactly
    the collision it exists to find — a fix that silently deletes a guard."""
    from aria_service.route_audit import find_duplicate_routes

    app = FastAPI()
    shared = APIRouter()

    @shared.get("/twice")
    async def twice_ep():
        return {}

    app.include_router(shared)
    app.include_router(shared)

    assert [p for p, _ in iter_routes(app)].count("/twice") == 2
    assert ("GET", "/twice") in find_duplicate_routes(app)


def test_a_self_referential_container_terminates():
    """An app mounted into itself must end the branch, not exhaust the stack."""
    class Cyclic:
        path = "/loop"

        def __init__(self):
            self.routes = [self]

    assert list(iter_routes(Cyclic())) == []


def test_iter_routes_accepts_a_bare_router():
    """Callers pass routers as well as apps; a router has no include wrapper."""
    router = APIRouter(prefix="/r")

    @router.get("/x")
    async def x_ep():
        return {}

    assert [p for p, _ in iter_routes(router)] == ["/r/x"]
