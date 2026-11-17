"""Explicit API process roles and opt-in routing for supervised readers."""

from enum import Enum
from functools import lru_cache
from typing import Callable, TypeVar, overload

from fastapi.routing import APIRoute


class ApiRole(str, Enum):
    PRIMARY = "primary"
    READER = "reader"


Endpoint = TypeVar("Endpoint", bound=Callable)


@overload
def reader(endpoint: Endpoint) -> Endpoint: ...


@overload
def reader(**path_values: str) -> Callable[[Endpoint], Endpoint]: ...


def reader(endpoint=None, **path_values):
    """Allow this GET on a reader; undecorated/new routes stay on the primary."""

    def declare(function):
        function.__anylist_reader__ = path_values
        return function

    return declare(endpoint) if endpoint is not None else declare


class ReaderRoutes:
    def __init__(self, routes):
        # Retain router order, including primary routes shadowing parameter paths.
        # Wrapped endpoints (e.g. rate-limit decorators) retain their single owner.
        policies = []
        for route in routes:
            if not isinstance(route, APIRoute) or "GET" not in route.methods:
                continue
            constraints = getattr(route.endpoint, "__anylist_reader__", None)
            if constraints is not None and (
                constraints.keys() - route.path_regex.groupindex.keys()
                or any(not isinstance(value, str) for value in constraints.values())
            ):
                raise ValueError(f"Invalid reader path constraints on {route.path}")
            policies.append(
                (
                    route.path_regex,
                    dict(constraints)
                    if constraints is not None
                    and not hasattr(route.endpoint, "__wrapped__")
                    else None,
                )
            )
        self.routes = tuple(policies)
        self.allows = lru_cache(maxsize=4096)(self._allows)

    def _allows(self, path, method):
        if method != "GET":
            return False
        for pattern, constraints in self.routes:
            match = pattern.fullmatch(path)
            if match:
                return constraints is not None and all(
                    match.group(name) == value for name, value in constraints.items()
                )
        return False
