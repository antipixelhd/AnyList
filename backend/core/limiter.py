from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware

# Single shared limiter instance imported by main.py (app setup)
# and by routers that need per-endpoint rate limits.
limiter = Limiter(key_func=get_remote_address)


class GlobalRateLimitMiddleware:
    """Endpoint decorators enforce route limits without a second route scan.

    Retain SlowAPI's middleware when global limits are configured, including
    limits supplied through its environment configuration. The attribute check
    follows the pinned SlowAPI middleware's own distinction between global and
    decorator-owned limits; headers, keys and storage stay owned by SlowAPI.
    """
    def __init__(self, app):
        self.app = app
        self.global_limits = SlowAPIMiddleware(app)

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            current = scope['app'].state.limiter
            if current.enabled and (current._default_limits or current._application_limits):
                return await self.global_limits(scope, receive, send)
        return await self.app(scope, receive, send)
