from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware


# Single shared limiter instance imported by main.py (app setup)
# and by routers that need per-endpoint rate limits.
class ApiLimiter(Limiter):
    """Expose our middleware policy from public constructor/configuration inputs."""

    def __init__(
        self,
        key_func=get_remote_address,
        *,
        default_limits=(),
        application_limits=(),
        **kwargs,
    ):
        super().__init__(
            key_func=key_func,
            default_limits=list(default_limits),
            application_limits=list(application_limits),
            **kwargs,
        )
        self.has_global_limits = bool(
            default_limits
            or application_limits
            or self.get_app_config("RATELIMIT_DEFAULT", None)
            or self.get_app_config("RATELIMIT_APPLICATION", None)
        )


limiter = ApiLimiter(key_func=get_remote_address)


class GlobalRateLimitMiddleware:
    """Endpoint decorators enforce route limits without a second route scan.

    Global limits, including environment/file configuration, retain SlowAPI's
    middleware. Headers, keys and storage remain owned by the library.
    """

    def __init__(self, app):
        self.app = app
        self.global_limits = SlowAPIMiddleware(app)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            current = scope["app"].state.limiter
            if current.enabled and current.has_global_limits:
                return await self.global_limits(scope, receive, send)
        return await self.app(scope, receive, send)
