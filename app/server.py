"""Servidor HTTP: recebe as leituras do medidor, serve a API e o painel web."""
from __future__ import annotations

import base64
import gzip
import hmac
import json
import logging
import mimetypes
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .config import Config
from .mqtt_bridge import MqttBridge
from .service import MAX_BODY, ApiError, Core, Diagnostics, Reply, discard  # noqa: F401
from .simulate import HouseSim
from .solar import DemoSolar, SolarSource
from .store import Store
from .timeutil import Clock

log = logging.getLogger("painel.http")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
INGEST_PATHS = ("/api/ingest", "/ingest", "/api/energia/medidor", "/api/insert.php", "/insert.php")
DEMO_ID = "DEMO"
INGRESS_PEERS = ("172.30.32.2", "127.0.0.1", "::1")     # de onde o Home Assistant (ingress) fala com o add-on


HttpError = ApiError


class App:
    """Junta banco, MQTT e tarefas periódicas."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.clock = Clock(cfg.tz)
        self.store = Store(cfg.db_path, self.clock, max_power_kw=cfg.max_power_kw,
                           offline_after_s=cfg.offline_after_s, tariff_default=cfg.tariff_default)
        self.core = Core(self.store, counter_unit=cfg.counter_unit, raw_retention_days=cfg.raw_retention_days,
                         protected_device=DEMO_ID if cfg.demo else None)
        self.diag = self.core.diag
        self.started = self.core.started
        self.mqtt = MqttBridge(cfg, self._on_mqtt_message, self.store.devices, self.store.snapshot)
        self.store.add_listener(self.mqtt.on_store_event)
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self.demo_state = "off"
        self.solar = self._make_solar()
        self.core.solar = self.solar

    # ------------------------------------------------------------------ ciclo de vida
    def start(self) -> None:
        self.mqtt.start()
        t = threading.Thread(target=self._maintenance_loop, name="manutencao", daemon=True)
        t.start()
        self._threads.append(t)
        if self.solar is not None:
            t = threading.Thread(target=self.solar.run, args=(self._stop,), name="solar", daemon=True)
            t.start()
            self._threads.append(t)
        if self.cfg.demo:
            t = threading.Thread(target=self._demo_loop, name="demo", daemon=True)
            t.start()
            self._threads.append(t)

    def _make_solar(self):
        cfg = self.cfg
        if cfg.solar_urls:
            return SolarSource(cfg.solar_urls, self.clock, poll_s=cfg.solar_poll_s, auto=cfg.solar_auto)
        if cfg.demo and cfg.demo_solar_kwp > 0:
            return DemoSolar(self.clock, HouseSim(self.clock, device_id=DEMO_ID, solar_kwp=cfg.demo_solar_kwp).solar)
        return None

    def stop(self) -> None:
        self._stop.set()
        self.mqtt.stop()

    def _maintenance_loop(self) -> None:
        while not self._stop.wait(10):
            try:
                self.core.maintenance()
            except Exception:
                log.exception("Erro na manutenção periódica")

    # ------------------------------------------------------------------ ingestão
    def ingest_raw(self, body: bytes, query: str, source: str, **meta: Any) -> Dict[str, Any]:
        return self.core.ingest_raw(body, query, source, **meta)

    def _on_mqtt_message(self, payload: bytes, topic: str) -> None:
        self.ingest_raw(payload[:MAX_BODY], "", "mqtt", topic=topic)

    # ------------------------------------------------------------------ modo demonstração
    def _demo_loop(self) -> None:
        try:
            sim = HouseSim(self.clock, device_id=DEMO_ID, solar_kwp=self.cfg.demo_solar_kwp)
            now = int(time.time())
            st = self.store.state(DEMO_ID)
            start = now - 35 * 86400
            if st is not None and st.last_ts:
                sim.resume(st.base, st.last_ts)
                start = st.last_ts + 60
            else:
                sim.reading(start - 60)
            if start < now - 60:
                self.demo_state = "gerando histórico"
                log.info("DEMO: gerando histórico simulado (%d dias)...", round((now - start) / 86400.0))
                t0 = time.time()
                n = 0
                batch: List[Tuple[int, Dict[str, Any]]] = []
                for ts in range(start, now - 30, 60):
                    batch.append((ts, self._demo_reading(sim, ts)))
                    if len(batch) >= 720:          # meio dia por vez: não trava a chegada de leituras reais
                        n += self.store.ingest_many(batch, source="demo")
                        batch = []
                        if self._stop.is_set():
                            return
                if batch:
                    n += self.store.ingest_many(batch, source="demo")
                log.info("DEMO: %d leituras simuladas em %.1f s", n, time.time() - t0)
            if self.store.state(DEMO_ID) and not self.store.state(DEMO_ID).name:
                self.store.update_device(DEMO_ID, name="Demonstração (simulado)")
            self.demo_state = "ao vivo"
            while not self._stop.wait(10):
                self.store.ingest(self._demo_reading(sim, int(time.time())), source="demo")
        except Exception:
            self.demo_state = "erro"
            log.exception("DEMO: erro no simulador")

    @staticmethod
    def _demo_reading(sim: HouseSim, ts: int) -> Dict[str, Any]:
        from .parser import normalize
        return normalize(sim.reading(ts))

    # ------------------------------------------------------------------ respostas da API
    def api_status(self, host: str = "") -> Dict[str, Any]:
        # endereço pelo qual o painel foi aberto: é o que o medidor deve usar (inclui a porta publicada pelo Docker)
        hostname, _, port = (host or "").rpartition(":") if ":" in (host or "") and not (host or "").endswith("]") else (host or "", "", "")
        hostname = hostname.strip("[]")
        public_port = int(port) if port.isdigit() else (self.cfg.http_port if not host else 80)
        if self.cfg.addon:
            # add-on: o painel passa pelo ingress do Home Assistant, mas o medidor usa a porta publicada
            hostname = self.cfg.public_host or hostname
            public_port = self.cfg.public_ingest_port or self.cfg.ingest_port or public_port
        out = self.core.status()
        out.update({
            "host": "addon" if self.cfg.addon else "standalone",
            "mqtt": self.mqtt.status(),
            "demo": self.demo_state,
            "ingest": {
                "http_port": public_port,
                "mqtt_port": self.cfg.mqtt_public_port or self.cfg.mqtt_port,
                "path": "/api/ingest" + ("/" + self.cfg.ingest_token if self.cfg.ingest_token else ""),
                "token": bool(self.cfg.ingest_token),
                "host": hostname,
                "mqtt_topic": self.cfg.mqtt_topics_in[0] if self.cfg.mqtt_topics_in else None,
                "mqtt_no_login": self.cfg.meter_needs_no_login,
            },
            "auth": bool(self.cfg.dash_user) or self.cfg.addon,
        })
        return out

    def resolve_period(self, period: str, date_str: Optional[str], now: Optional[float] = None) -> Dict[str, Any]:
        return self.core.resolve_period(period, date_str, now)

    def api_consumo(self, dev: str, period: str, date_str: Optional[str]) -> Dict[str, Any]:
        return self.core.consumo(dev, period, date_str)


# ---------------------------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PainelEnergia/" + __version__
    sys_version = ""
    timeout = 30

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    def finish(self) -> None:
        try:
            super().finish()
        finally:
            self.app.store.close_thread()

    # ------------------------------------------------------------------ utilidades
    def _send(self, code: int, body: bytes, ctype: str, headers: Optional[Dict[str, str]] = None,
              compress: bool = True) -> None:
        extra = dict(headers or {})
        if compress and len(body) > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = gzip.compress(body, 5)
            extra["Content-Encoding"] = "gzip"
            extra["Vary"] = "Accept-Encoding"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _error(self, code: int, message: str) -> None:
        self._json({"error": message}, code)

    def _read_body(self) -> bytes:
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            data = b""
            while True:
                line = self.rfile.readline(1024).strip()
                if not line:
                    break
                size = int(line.split(b";")[0], 16)
                if size == 0:
                    while self.rfile.readline(1024).strip():
                        pass
                    break
                if len(data) + size > MAX_BODY:
                    raise HttpError(413, "mensagem grande demais")
                data += self.rfile.read(size)
                self.rfile.readline(8)
            return data
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            if self.command != "POST":
                return b""
            # cliente sem Content-Length: lê o que chegar e encerra a conexão
            self.close_connection = True
            self.connection.settimeout(1.5)
            try:
                return self.rfile.read1(MAX_BODY)
            except Exception:
                return b""
        try:
            length = int(raw_len)
        except ValueError:
            raise HttpError(400, "Content-Length inválido")
        if length > MAX_BODY:
            raise HttpError(413, "mensagem grande demais")
        return self.rfile.read(length) if length > 0 else b""

    @property
    def view_mode(self) -> bool:
        """Porta do painel fora do Home Assistant (add-on)."""
        return getattr(self.server, "mode", "") == "view"

    def _authorized(self) -> bool:
        cfg = self.app.cfg
        if cfg.addon and not self.view_mode:
            return True                 # pelo ingress, quem confere o login é o Home Assistant
        if not cfg.dash_user:
            return True
        header = self.headers.get("Authorization") or ""
        if header.lower().startswith("basic "):
            try:
                user, _, pwd = base64.b64decode(header[6:].strip()).decode("utf-8").partition(":")
            except Exception:
                return False
            return hmac.compare_digest(user, cfg.dash_user) and hmac.compare_digest(pwd, cfg.dash_password)
        return False

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        body = "Acesso restrito.".encode("utf-8")
        self._send(401, body, "text/plain; charset=utf-8",
                   {"WWW-Authenticate": 'Basic realm="Painel de Energia", charset="UTF-8"'})
        return False

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin or self.app.cfg.addon:      # no add-on quem confere o login e a origem é o Home Assistant
            return True
        return urlsplit(origin).netloc.lower() == (self.headers.get("Host") or "").lower()

    def _q(self, name: str, default: Optional[str] = None) -> Optional[str]:
        vals = self.query.get(name)
        return vals[0] if vals else default

    # ------------------------------------------------------------------ métodos HTTP
    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("POST")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        try:
            parts = urlsplit(self.path)
            self.route = unquote(parts.path) or "/"
            self.raw_query = parts.query
            self.query = parse_qs(parts.query, keep_blank_values=True)
            self._route(method)
        except HttpError as exc:
            self._error(exc.code, exc.message)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception:
            log.exception("Erro ao atender %s %s", method, self.path)
            try:
                self._error(500, "erro interno")
            except Exception:
                self.close_connection = True

    def _route(self, method: str) -> None:
        route = self.route.rstrip("/") or "/"
        low = route.lower()

        # ---- ingestão (sem login: o medidor não sabe autenticar) -------------------------
        for base in INGEST_PATHS:
            if low == base or low.startswith(base + "/"):
                return self._ingest(method, route[len(base):].strip("/"))
        if low == "/api/health":
            return self._json({"ok": True, "version": __version__})
        if getattr(self.server, "ingest_only", False):
            # porta só do medidor: nada do painel nem da API é servido aqui
            if method == "POST" or (method == "GET" and self.raw_query):
                return self._ingest(method, "")
            raise HttpError(404, "esta porta só recebe as leituras do medidor; abra o painel pelo Home Assistant")
        if self.app.cfg.addon and not self.view_mode and self.client_address[0] not in INGRESS_PEERS:
            raise HttpError(403, "abra o painel pelo Home Assistant")

        if low.startswith("/api/"):
            if not self._require_auth():
                return
            if method != "GET" and self.view_mode and self.app.cfg.view_readonly:
                raise HttpError(403, "este endereço do painel é só para visualizar; altere pelo Home Assistant")
            if method != "GET" and not self._same_origin():
                raise HttpError(403, "origem não permitida")
            return self._api(method, route)

        # ---- qualquer outro caminho: se parecer leitura do medidor, aceita ----------------
        if method == "POST" or (method == "GET" and self.raw_query and self._looks_like_meter()):
            return self._ingest(method, "")
        if method != "GET":
            raise HttpError(405, "método não permitido")
        if not self._require_auth():
            return
        return self._static(route)

    def _looks_like_meter(self) -> bool:
        keys = {k.lower() for k in self.query}
        return bool(keys & {"uarms", "pt", "pa", "iarms", "json", "data"})

    # ------------------------------------------------------------------ ingestão HTTP
    def _ingest(self, method: str, suffix: str) -> None:
        cfg = self.app.cfg
        body = self._read_body() if method == "POST" else b""
        meta = {"method": self.command, "path": self.route[:120], "remote": self.client_address[0],
                "ctype": (self.headers.get("Content-Type") or "")[:80]}
        if cfg.ingest_token:
            given = suffix or self._q("token") or self.headers.get("X-Token") or ""
            if not hmac.compare_digest(given, cfg.ingest_token):
                self.app.core.reject("http", "token de ingestão ausente ou incorreto", **meta)
                raise HttpError(403, "token inválido")
        query = self.raw_query
        if not body and not query:
            if method == "GET":
                # navegador abrindo o endereço: explica em vez de registrar erro
                return self._json({"ok": True, "info": "Endpoint de ingestão do Painel de Energia. Configure o "
                                                       "medidor para enviar (POST ou GET) para este endereço."})
            self.app.diag.add(False, "http", error="mensagem vazia (sem corpo e sem parâmetros)", **meta)
            raise HttpError(400, "mensagem vazia")
        res = self.app.ingest_raw(body, query, "http", **meta)
        self.close_connection = True
        if res["ok"]:
            self._send(200, b"OK", "text/plain; charset=utf-8", {"Connection": "close"}, compress=False)
        elif res.get("pending"):
            # a mensagem está certa; o motivo é do lado do servidor, então o medidor não recebe um erro
            self._send(200, ("RECEBIDA, MAS NAO GRAVADA: " + res["error"]).encode("utf-8"),
                       "text/plain; charset=utf-8", {"Connection": "close"}, compress=False)
        else:
            self._send(400, ("ERRO: " + res["error"]).encode("utf-8"), "text/plain; charset=utf-8",
                       {"Connection": "close"}, compress=False)

    # ------------------------------------------------------------------ API
    def _api(self, method: str, route: str) -> None:
        app = self.app
        low = route.lower()
        if method == "GET":
            if low == "/api/status":
                out = app.api_status(self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "")
                if self.view_mode:
                    out["readonly"] = bool(app.cfg.view_readonly)
                    out["auth"] = bool(app.cfg.dash_user)
                    out["direct"] = True
                return self._json(out)
            out = app.core.api_get(route, {k: v[0] for k, v in self.query.items() if v})
            if isinstance(out, Reply):
                return self._download(out)
            return self._json(out)
        body = self._read_body()
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except ValueError:
            raise HttpError(400, "JSON inválido")
        return self._json(app.core.api_write(method, route, data))

    def _download(self, reply: Reply) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", reply.ctype)
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % reply.filename)
            self.send_header("Cache-Control", "no-store")
            if reply.size is not None and reply.chunks is None:
                self.send_header("Content-Length", str(reply.size))
            else:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if reply.chunks is not None:
                for chunk in reply.chunks:
                    self.wfile.write(chunk)
            elif reply.file:
                with open(reply.file, "rb") as fh:
                    while True:
                        chunk = fh.read(256 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
        finally:
            discard(reply.file)

    # ------------------------------------------------------------------ arquivos do painel
    def _static(self, route: str) -> None:
        rel = "index.html" if route in ("/", "") else route.lstrip("/")
        path = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not (path == STATIC_DIR or path.startswith(STATIC_DIR + os.sep)) or not os.path.isfile(path):
            raise HttpError(404, "não encontrado")
        stat = os.stat(path)
        etag = '"%x-%x"' % (int(stat.st_mtime), stat.st_size)
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json",
                                                  "application/manifest+json", "image/svg+xml"):
            if "charset" not in ctype and not ctype.startswith("image/"):
                ctype += "; charset=utf-8"
        body = _read_static(path, stat.st_mtime)
        compress = not ctype.startswith(("image/png", "image/jpeg", "font/"))
        self._send(200, body, ctype, {"ETag": etag, "Cache-Control": "no-cache"}, compress=compress)


_static_cache: Dict[str, Tuple[float, bytes]] = {}


def _read_static(path: str, mtime: float) -> bytes:
    hit = _static_cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(path, "rb") as fh:
        data = fh.read()
    _static_cache[path] = (mtime, data)
    return data


mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("image/svg+xml", ".svg")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, app: App, port: Optional[int] = None, ingest_only: bool = False, mode: str = ""):
        self.app = app
        self.ingest_only = ingest_only
        self.mode = mode
        super().__init__((app.cfg.http_host, app.cfg.http_port if port is None else port), Handler)

    def handle_error(self, request, client_address) -> None:  # conexões quebradas não poluem o log
        log.debug("Conexão encerrada com erro: %s", client_address, exc_info=True)
