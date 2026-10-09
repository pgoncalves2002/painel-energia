"""Ponto de entrada: python -m app"""
from __future__ import annotations

import logging
import os
import signal
import sqlite3
import sys
import threading

from . import __version__
from .config import Config
from .server import App, Server


def main() -> int:
    cfg = Config.from_env()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    log = logging.getLogger("painel")
    try:
        os.makedirs(cfg.data_dir, exist_ok=True)
        app = App(cfg)
    except (OSError, sqlite3.Error) as exc:
        log.error("Não foi possível abrir o banco de dados em %s: %s. Confira se a pasta existe e pode ser gravada "
                  "pelo usuário do processo.", cfg.db_path, exc)
        return 1
    # horário dos logs no fuso configurado, mesmo que a imagem não traga a base de fusos do sistema
    for handler in logging.getLogger().handlers:
        if handler.formatter is not None:
            handler.formatter.converter = lambda ts: app.clock.local(ts).timetuple()
    try:
        server = Server(app)
    except OSError as exc:
        log.error("Não foi possível abrir a porta %s: %s", cfg.http_port, exc)
        return 1

    meter_server = None
    if cfg.ingest_port and cfg.ingest_port != cfg.http_port:
        try:
            meter_server = Server(app, port=cfg.ingest_port, ingest_only=True)
        except OSError as exc:
            log.error("Não foi possível abrir a porta %s (medidor): %s", cfg.ingest_port, exc)
            return 1
        threading.Thread(target=meter_server.serve_forever, kwargs={"poll_interval": 0.5}, name="medidor", daemon=True).start()

    view_server = None
    if cfg.addon and cfg.view_port:
        try:
            view_server = Server(app, port=cfg.view_port, mode="view")
        except OSError as exc:
            log.error("Não foi possível abrir a porta %s (painel direto): %s", cfg.view_port, exc)
        else:
            threading.Thread(target=view_server.serve_forever, kwargs={"poll_interval": 0.5}, name="painel-direto",
                             daemon=True).start()
            log.info("Painel direto (sem o login do Home Assistant) na porta %s%s%s", cfg.view_port,
                     ", com senha" if cfg.dash_user else ", sem senha",
                     ", só visualização" if cfg.view_readonly else "")

    def shutdown(signum, _frame):
        log.info("Encerrando (sinal %s)...", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()
        for extra in (meter_server, view_server):
            if extra is not None:
                threading.Thread(target=extra.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    app.start()
    log.info("Painel de Energia %s", __version__)
    # dentro do Docker a porta vista de fora pode ser outra (PAINEL_PORT); com PUBLIC_URL o endereço sai certo
    base = cfg.public_url or "http://<ip-do-servidor>:%s" % cfg.http_port
    if cfg.addon:
        meter = "http://%s:%s" % (cfg.public_host or "<ip-do-home-assistant>", cfg.public_ingest_port or cfg.ingest_port)
        log.info("Painel:   abra pelo Home Assistant (barra lateral ou página do add-on)")
        log.info("Medidor:  HTTP POST/GET para %s/api/ingest", meter)
    else:
        log.info("Painel:   %s/", base)
        log.info("Medidor:  HTTP POST/GET para %s/api/ingest%s", base, "/<token>" if cfg.ingest_token else "")
    if cfg.mqtt_enabled:
        log.info("MQTT:     broker %s:%s, tópico do medidor: %s", cfg.mqtt_host, cfg.mqtt_port,
                 ", ".join(cfg.mqtt_topics_in))
    else:
        log.info("MQTT:     desativado (defina MQTT_HOST para ligar a integração com o Home Assistant)")
    log.info("Fuso horário: %s | banco: %s", app.clock.name, cfg.db_path)
    if cfg.demo:
        log.info("Modo DEMO ligado: um medidor simulado ('DEMO') será criado.")
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        app.stop()
        server.server_close()
        log.info("Encerrado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
