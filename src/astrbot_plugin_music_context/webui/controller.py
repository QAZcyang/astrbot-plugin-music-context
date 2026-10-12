"""Authenticated AstrBot bridge handlers; no separate server or credentials."""

import asyncio
import logging
from collections.abc import Callable

from ..config_service import ConfigConflictError
from .service import DashboardService

PLUGIN_NAME = "astrbot_plugin_music_context"


class DashboardController:
    def __init__(self, service: DashboardService):
        self.service = service

    def register(self, context: object) -> bool:
        """Older hosts may still run chat without the optional Pages API."""
        try:
            from astrbot.api.web import error_response, json_response, request
        except ImportError:
            return False
        register = getattr(context, "register_web_api", None)
        if not callable(register):
            return False
        self.error_response = error_response
        self.json_response = json_response
        self.request = request
        routes = {
            "overview": (self.overview, "GET"),
            "lyrics": (self.lyrics, "GET"),
            "lyrics/detail": (self.lyric_detail, "GET"),
            "contexts": (self.contexts, "GET"),
            "contexts/<identifier>": (self.context_detail, "GET"),
            "history": (self.history, "GET"),
            "history/clear": (self.clear_history, "POST"),
            "cache/clear": (self.clear_cache, "POST"),
        }
        if self.service.config_service is not None:
            routes.update(
                {
                    "config": (self.configuration, "GET"),
                    "config/save": (self.save_configuration, "POST"),
                    "config/reset": (self.reset_configuration, "POST"),
                    "processor/test": (self.test_processor, "POST"),
                    "processor/debug": (self.processor_debug, "GET"),
                }
            )
        for endpoint, (handler, method) in routes.items():
            register(
                f"/{PLUGIN_NAME}/{endpoint}", handler, [method], "Music management"
            )
        return True

    def unregister(self, context: object) -> None:
        """Release only this instance's handlers; there is no unregister helper."""
        apis = getattr(context, "registered_web_apis", None)
        if isinstance(apis, list):
            apis[:] = [
                api for api in apis if getattr(api[1], "__self__", None) is not self
            ]

    async def _call(self, operation: Callable, *args: object, **kwargs: object):
        try:
            if not self.service.state.active:
                return self.error_response("Plugin stopped", status_code=503)
            async with asyncio.timeout(2.0):
                result = await asyncio.to_thread(operation, *args, **kwargs)
            return self.json_response(result, headers={"Cache-Control": "no-store"})
        except TimeoutError:
            return self.error_response(
                "Management operation timed out", status_code=504
            )
        except Exception as exc:  # noqa: BLE001 - sanitize management errors
            logging.getLogger("astrbot").warning(
                "Music management failed (%s)", type(exc).__name__
            )
            return self.error_response("Management operation failed", status_code=500)

    async def overview(self):
        return await self._call(self.service.overview)

    async def lyrics(self):
        offset = self.request.query.get("offset", 0, type=int)
        limit = self.request.query.get("limit", 50, type=int)
        if not 0 <= offset <= 4096 or not 1 <= limit <= 100:
            return self.error_response("Invalid pagination", status_code=400)
        return await self._call(self.service.lyrics, offset=offset, limit=limit)

    async def lyric_detail(self):
        source = self.request.query.get("source")
        track_id = self.request.query.get("track_id")
        if not all(
            isinstance(value, str) and 0 < len(value) <= 128
            for value in (source, track_id)
        ):
            return self.error_response("Invalid song identity", status_code=400)
        return await self._call(self.service.lyric_detail, source, track_id)

    async def contexts(self):
        return await self._call(self.service.contexts)

    async def context_detail(self, identifier: str):
        if (
            not identifier.isascii()
            or not identifier.isdecimal()
            or len(identifier) > 12
        ):
            return self.error_response("Invalid context identifier", status_code=400)
        return await self._call(self.service.context_detail, int(identifier))

    async def history(self):
        return await self._call(self.service.history)

    async def _confirmed(self) -> bool:
        data = await self.request.json(default={})
        return isinstance(data, dict) and data.get("confirm") is True

    async def clear_history(self):
        if not await self._confirmed():
            return self.error_response("Confirmation required", status_code=400)

        def clear():
            if not self.service.clear_history():
                raise OSError("History could not be cleared on disk")
            return {"cleared": True}

        return await self._call(clear)

    async def clear_cache(self):
        if not await self._confirmed():
            return self.error_response("Confirmation required", status_code=400)

        def clear():
            if not self.service.clear_cache():
                raise OSError("Cache could not be fully cleared")
            return {"cleared": True}

        return await self._call(clear)

    async def configuration(self):
        return await self._call(self.service.config_service.snapshot)

    async def _configuration_action(self, action: str):
        if not self.service.state.active:
            return self.error_response("Plugin stopped", status_code=503)
        try:
            body = await self.request.json(default={})
            if not isinstance(body, dict):
                raise TypeError
            revision = body.get("revision")
            if not isinstance(revision, str):
                raise TypeError
            service = self.service.config_service
            if action == "save":
                # No short management timeout: a file commit must finish before reply.
                result = await service.update(body.get("values"), revision=revision)
            else:
                if body.get("confirm") is not True:
                    raise ValueError
                result = await service.reset(revision=revision)
            return self.json_response(result, headers={"Cache-Control": "no-store"})
        except ConfigConflictError:
            return self.error_response(
                "Configuration changed; reload before saving", status_code=409
            )
        except (ValueError, TypeError):
            return self.error_response(
                "Invalid configuration; check field types and ranges", status_code=400
            )
        except Exception:  # noqa: BLE001 - do not expose configuration or filesystem details
            return self.error_response(
                "Configuration could not be saved", status_code=500
            )

    async def save_configuration(self):
        return await self._configuration_action("save")

    async def reset_configuration(self):
        return await self._configuration_action("reset")

    async def processor_debug(self):
        return await self._call(self.service.processor_debug)

    async def test_processor(self):
        if not self.service.state.active:
            return self.error_response("Plugin stopped", status_code=503)
        if not await self._confirmed():
            return self.error_response("Confirmation required", status_code=400)
        if self.service.processor is None:
            return self.error_response("Processor unavailable", status_code=503)
        result = await self.service.processor.test_connection()
        return self.json_response(result, headers={"Cache-Control": "no-store"})
