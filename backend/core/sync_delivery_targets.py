"""Restrict a recovered cycle delivery to its unacknowledged destination."""
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import false, true

_destination: ContextVar[str | None] = ContextVar("sync_delivery_destination", default=None)


@contextmanager
def destination_scope(destination):
    token = _destination.set(destination)
    try:
        yield
    finally:
        _destination.reset(token)


def connection_matches(connection_id):
    target = _destination.get()
    return target is None or target == f"connection:{connection_id}"


def connection_clause(column):
    target = _destination.get()
    if target is None:
        return true()
    if target.startswith("connection:"):
        return column == int(target.split(":", 1)[1])
    return false()


def cloud_matches(provider):
    target = _destination.get()
    return target is None or target == str(getattr(provider, "value", provider))


def dispatch_queues():
    return _destination.get() in (None, "queues")


def queue_handoff():
    return _destination.get() == "queues"
