"""API do painel dentro do Home Assistant: as mesmas rotas do servidor próprio, com o login do HA."""
from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import API_URL, DOMAIN
from .core.service import ApiError, Reply, discard

_LOGGER = logging.getLogger(__name__)


def _api_get(core: Any, route: str, params: dict) -> Any:
    """Roda na thread do banco; arquivos para download já saem gravados em disco."""
    out = core.api_get(route, params)
    return out.to_file() if isinstance(out, Reply) else out


class PainelApiView(HomeAssistantView):
    url = API_URL + "/{path:.+}"
    name = "api:painel_energia"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def _hub(self) -> Any:
        hub = self.hass.data.get(DOMAIN)
        if hub is None or hub.core is None:
            raise ApiError(503, "a integração Painel de Energia não está carregada")
        return hub

    async def get(self, request: web.Request, path: str) -> web.StreamResponse:
        route = "/api/" + path
        try:
            hub = self._hub()
            if route.rstrip("/") == "/api/status":
                return self.json(await hub.async_status(request))
            out = await hub.run(_api_get, hub.core, route, dict(request.query))
        except ApiError as exc:
            return self.json({"error": exc.message}, exc.code)
        except Exception:
            _LOGGER.exception("Erro ao atender GET %s", route)
            return self.json({"error": "erro interno"}, 500)
        if isinstance(out, Reply):
            return await self._download(hub, request, out)
        return self.json(out)

    async def _download(self, hub: Any, request: web.Request, reply: Reply) -> web.StreamResponse:
        resp = web.StreamResponse(headers={
            "Content-Type": reply.ctype, "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="%s"' % reply.filename})
        fh = None
        try:
            if reply.size is not None:
                resp.content_length = reply.size
            await resp.prepare(request)
            fh = await hub.run(open, reply.file, "rb")
            while True:
                chunk = await hub.run(fh.read, 256 * 1024)
                if not chunk:
                    break
                await resp.write(chunk)
            await resp.write_eof()
        finally:
            if fh is not None:
                await hub.run(fh.close)
            await hub.run(discard, reply.file)
        return resp

    async def _write(self, method: str, request: web.Request, path: str) -> web.Response:
        user = request.get("hass_user")
        if user is None or not user.is_admin:
            return self.json({"error": "só administradores do Home Assistant podem alterar o painel"}, 403)
        try:
            data = await request.json() if request.can_read_body else {}
        except ValueError:
            return self.json({"error": "JSON inválido"}, 400)
        try:
            hub = self._hub()
            return self.json(await hub.run(hub.core.api_write, method, "/api/" + path, data))
        except ApiError as exc:
            return self.json({"error": exc.message}, exc.code)
        except Exception:
            _LOGGER.exception("Erro ao atender %s /api/%s", method, path)
            return self.json({"error": "erro interno"}, 500)

    async def post(self, request: web.Request, path: str) -> web.Response:
        return await self._write("POST", request, path)

    async def delete(self, request: web.Request, path: str) -> web.Response:
        return await self._write("DELETE", request, path)
