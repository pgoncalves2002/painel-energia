import base64
import http.client
import json
import os
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from urllib.parse import quote, urlencode

from app.config import Config
from app.server import App, Server
from tests.test_parser import SM3W, SMW


class ServerCase(unittest.TestCase):
    ENV = {}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        env = {"DATA_DIR": cls.tmp.name, "HTTP_HOST": "127.0.0.1", "HTTP_PORT": "0", "TZ": "America/Sao_Paulo"}
        env.update(cls.ENV)
        cls.cfg = Config.from_env(env)
        cls.app = App(cls.cfg)
        cls.server = Server(cls.app)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        cls.thread.start()
        cls.app.start()

    @classmethod
    def tearDownClass(cls):
        cls.app.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.app.store.close_thread()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, dict(resp.getheaders()), data
        finally:
            conn.close()

    def get_json(self, path, headers=None):
        status, _h, data = self.request("GET", path, headers=headers)
        self.assertEqual(status, 200, data[:300])
        return json.loads(data)

    def post_json(self, path, obj, headers=None, method="POST"):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        status, _h, data = self.request(method, path, body=json.dumps(obj).encode(), headers=h)
        return status, (json.loads(data) if data[:1] in (b"{", b"[") else data)


def meter(i, dev="casa", **over):
    """Mensagem do medidor trifásico na i-ésima leitura (contadores avançando)."""
    m = dict(SM3W)
    m.update({"id": dev, "pa": "410.20", "pb": "250.00", "pc": "120.50", "pt": "780.70",
              "uarms": "127.31", "ubrms": "126.02", "ucrms": "128.10",
              "iarms": "3.30", "ibrms": "2.01", "icrms": "0.97", "itrms": "6.28",
              "sa": "420.00", "sb": "255.00", "sc": "124.00", "st": "799.00", "pft": "0.98",
              "epa_c": "%.2f" % (10 + i * 0.01), "epb_c": "%.2f" % (20 + i * 0.01),
              "epc_c": "5.00", "ept_c": "%.2f" % (35 + i * 0.02), "yuaub": "120.00", "yuauc": "240.00",
              "yubuc": "120.00"})
    m.update(over)
    return m


class ConfigTest(unittest.TestCase):
    def test_medidor_sem_login_so_no_broker_que_acompanha_o_painel(self):
        base = {"MQTT_USERNAME": "painel", "MQTT_PASSWORD": "x"}
        self.assertTrue(Config.from_env(dict(base, MQTT_HOST="mosquitto")).meter_needs_no_login)
        self.assertFalse(Config.from_env(dict(base, MQTT_HOST="mosquitto", MQTT_METER_ANONYMOUS="false")).meter_needs_no_login)
        self.assertFalse(Config.from_env(dict(base, MQTT_HOST="192.168.0.20")).meter_needs_no_login)   # broker externo
        self.assertFalse(Config.from_env({"MQTT_HOST": "mosquitto"}).meter_needs_no_login)              # broker aberto
        self.assertFalse(Config.from_env({}).mqtt_enabled)

    def test_unidade_dos_contadores(self):
        self.assertEqual(Config.from_env({}).counter_scale, 1.0)
        self.assertEqual(Config.from_env({"COUNTER_UNIT": " Wh "}).counter_scale, 0.001)
        self.assertEqual(Config.from_env({"COUNTER_UNIT": "outra"}).counter_scale, 1.0)
        with tempfile.TemporaryDirectory() as tmp:
            app = App(Config.from_env({"DATA_DIR": tmp, "COUNTER_UNIT": "wh"}))
            try:
                self.assertTrue(app.ingest_raw(json.dumps(meter(0, ept_c="35020.00")).encode(), "", "http")["ok"])
                self.assertAlmostEqual(app.store.snapshot("casa")["reading"]["ept_c"], 35.02)
            finally:
                app.store.close_thread()


class RelogioAtrasadoTest(unittest.TestCase):
    def test_leitura_nao_gravada_aparece_nas_mensagens_recebidas(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            app = App(Config.from_env({"DATA_DIR": tmp, "TZ": "America/Sao_Paulo"}))
            try:
                self.assertTrue(app.ingest_raw(json.dumps(meter(0)).encode(), "", "http")["ok"])
                with mock.patch("app.store.time.time", return_value=time.time() - 3600):
                    res = app.ingest_raw(json.dumps(meter(1)).encode(), "", "http")
                self.assertEqual((res["ok"], res.get("pending")), (False, True))
                self.assertIn("relógio do servidor", res["error"])
                d = app.diag.dump()
                self.assertEqual((d["ok"], d["failed"]), (1, 1))
                self.assertEqual((d["recent"][0]["ok"], d["recent"][0]["device"]), (False, "casa"))
                # com a hora certa de volta, a leitura seguinte é gravada normalmente
                self.assertTrue(app.ingest_raw(json.dumps(meter(2)).encode(), "", "http")["ok"])
                self.assertEqual(app.store.snapshot("casa")["device"]["n_readings"], 2)
            finally:
                app.store.close_thread()


class IngestApiTest(ServerCase):
    def test_01_painel_vazio(self):
        st = self.get_json("/api/status")
        self.assertEqual(st["devices"], [])
        self.assertEqual(st["tz"], "America/Sao_Paulo")
        self.assertEqual(st["ingest"]["path"], "/api/ingest")
        status, _h, data = self.request("GET", "/api/live")
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(data))
        self.assertEqual(self.get_json("/api/health")["ok"], True)

    def test_02_formas_de_envio(self):
        # 1) POST com JSON puro
        status, _h, data = self.request("POST", "/api/ingest", json.dumps(meter(0)).encode(),
                                        {"Content-Type": "application/json"})
        self.assertEqual((status, data), (200, b"OK"))
        time.sleep(1.1)
        # 2) POST com JSON, mas Content-Type de formulário (JSON vira a chave)
        status, _h, data = self.request("POST", "/api/insert.php", (quote(json.dumps(meter(1))) + "=").encode(),
                                        {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 200, data)
        time.sleep(1.1)
        # 3) GET com pares chave=valor
        status, _h, data = self.request("GET", "/api/ingest?" + urlencode(meter(2)))
        self.assertEqual(status, 200, data)
        time.sleep(1.1)
        # 4) POST para um caminho qualquer (caminho errado no medidor ainda funciona)
        status, _h, data = self.request("POST", "/qualquer/coisa.php", json.dumps(meter(3)).encode())
        self.assertEqual(status, 200, data)
        time.sleep(1.1)
        # 5) GET na raiz com parâmetros do medidor
        status, _h, data = self.request("GET", "/?" + urlencode(meter(4)))
        self.assertEqual((status, data), (200, b"OK"))
        time.sleep(1.1)
        # 6) corpo em pedaços (Transfer-Encoding: chunked)
        raw = json.dumps(meter(5)).encode()
        half = len(raw) // 2
        body = b"%x\r\n%s\r\n%x\r\n%s\r\n0\r\n\r\n" % (half, raw[:half], len(raw) - half, raw[half:])
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"POST /api/ingest HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n" + body)
        self.assertIn(b"200", s.recv(200).split(b"\r\n")[0])
        s.close()
        time.sleep(1.1)
        # 7) POST sem Content-Length
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"POST /api/ingest HTTP/1.0\r\nHost: x\r\n\r\n" + json.dumps(meter(6)).encode())
        self.assertIn(b"200", s.recv(200).split(b"\r\n")[0])
        s.close()

        live = self.get_json("/api/live?device=casa")
        self.assertEqual(live["device"]["n_readings"], 7)
        self.assertEqual(live["device"]["model"], "SM-3W Lite")
        self.assertAlmostEqual(live["reading"]["pt"], 780.7)
        self.assertAlmostEqual(live["today"]["c_t"], 0.12, places=4)
        self.assertAlmostEqual(live["totals"]["c_a"], 0.06, places=4)
        diag = self.get_json("/api/diagnostics")
        self.assertEqual(diag["ok"], 7)
        self.assertEqual(diag["by_source"], {"http": 7})
        self.assertEqual(diag["recent"][0]["fields"], 39)

    def test_03_mensagens_invalidas(self):
        status, _h, data = self.request("POST", "/api/ingest", b"isto nao e um json")
        self.assertEqual(status, 400)
        self.assertTrue(data.startswith(b"ERRO"))
        status, _h, _d = self.request("POST", "/api/ingest", b"")
        self.assertEqual(status, 400)
        status, _h, _d = self.request("POST", "/api/ingest", b"x" * 70000)
        self.assertEqual(status, 413)
        status, _h, data = self.request("GET", "/api/ingest")
        self.assertEqual(status, 200)                 # navegador abrindo o endereço: só explica
        diag = self.get_json("/api/diagnostics")
        self.assertEqual(diag["failed"], 2)
        self.assertIn("isto nao", diag["recent"][1]["snippet"])
        self.assertEqual(self.get_json("/api/live?device=casa")["device"]["n_readings"], 7)

    def test_04_consultas(self):
        now = int(time.time())
        s = self.get_json("/api/summary?device=casa")
        self.assertAlmostEqual(s["today"]["c_t"], 0.12, places=4)
        self.assertAlmostEqual(s["today"]["cost"], round(0.12 * 0.95, 2))
        self.assertEqual(s["mode"], "consumo")
        chk = self.get_json("/api/check?device=casa")
        self.assertEqual((chk["status"], chk["counter_unit"], chk["hours"]), ("aguardando", "kwh", 24))
        ser = self.get_json("/api/series?device=casa&fields=pt,uarms,xx&from=%d" % (now - 600))
        self.assertEqual(len(ser["t"]), 7)
        self.assertEqual(sorted(ser["series"]), ["pt", "uarms"])
        e = self.get_json("/api/energy?device=casa&group=hour&from=%d" % (now - 7200))
        self.assertAlmostEqual(e["totals"]["c_t"], 0.12, places=4)
        self.assertEqual(e["tariff"], 0.95)
        for period, group in (("day", "hour"), ("week", "day"), ("month", "day"), ("year", "month")):
            c = self.get_json("/api/consumo?device=casa&period=%s" % period)
            self.assertEqual(c["period"]["group"], group)
            self.assertTrue(c["period"]["is_current"])
            self.assertAlmostEqual(c["totals"]["c_t"], 0.12, places=4)
            self.assertIn("prev_same_time", c)
        self.assertEqual(len(self.get_json("/api/consumo?device=casa&period=day")["rows"]), 24)
        self.assertEqual(len(self.get_json("/api/consumo?device=casa&period=week")["rows"]), 7)
        self.assertEqual(len(self.get_json("/api/consumo?device=casa&period=year")["rows"]), 12)
        old = self.get_json("/api/consumo?device=casa&period=month&date=2020-02-10")
        self.assertEqual((old["period"]["from_date"], old["period"]["to_date"]), ("2020-02-01", "2020-02-29"))
        self.assertFalse(old["period"]["is_current"])
        self.assertTrue(old["period"]["has_next"])
        self.assertEqual(self.request("GET", "/api/consumo?device=casa&period=decada")[0], 400)
        self.assertEqual(self.request("GET", "/api/consumo?device=casa&date=ontem")[0], 400)
        q = self.get_json("/api/quality?device=casa")
        self.assertEqual(q["v_nom"], 127.0)
        self.assertEqual(sorted(q["phases"]), ["a", "b", "c"])
        rows = self.get_json("/api/readings?device=casa&limit=3")
        self.assertEqual(len(rows["rows"]), 3)
        self.assertEqual(rows["fields"][0], "pa")
        self.assertEqual(len(self.get_json("/api/fields")["fields"]), 40)
        self.assertEqual(self.get_json("/api/events")["events"], [])
        self.assertEqual(self.request("GET", "/api/series?device=naoexiste")[0], 404)
        self.assertEqual(self.request("GET", "/api/nada")[0], 404)
        self.assertEqual(self.request("GET", "/api/series?device=casa&from=abc")[0], 400)

    def test_05_segundo_medidor_monofasico(self):
        status, _h, data = self.request("POST", "/api/ingest", json.dumps(SMW).encode())
        self.assertEqual(status, 200, data)
        devs = {d["id"]: d for d in self.get_json("/api/devices")["devices"]}
        self.assertEqual(sorted(devs), ["1", "casa"])
        self.assertEqual((devs["1"]["phases"], devs["1"]["model"]), (1, "SM-W Lite"))
        self.assertEqual(list(devs["1"]["labels"]), ["a"])

    def test_06_configuracoes_e_medidores(self):
        status, res = self.post_json("/api/settings", {"tariff": 1.12, "credit": 0.5, "mode": "bidirecional"})
        self.assertEqual(status, 200)
        self.assertEqual(res["settings"]["tariff"], 1.12)
        self.assertEqual(self.get_json("/api/summary?device=casa")["mode"], "bidirecional")
        self.assertEqual(self.post_json("/api/settings", {"tariff": "abc"})[0], 400)
        self.assertEqual(self.post_json("/api/settings", {"mode": "x"})[0], 400)
        self.assertEqual(self.request("POST", "/api/settings", b"{nao json", {"Content-Type": "application/json"})[0], 400)
        # origem de outro site não pode alterar nada
        self.assertEqual(self.post_json("/api/settings", {"tariff": 9}, {"Origin": "http://malicioso.example"})[0], 403)
        self.assertEqual(self.get_json("/api/status")["settings"]["tariff"], 1.12)
        status, res = self.post_json("/api/devices/casa", {"name": "Casa da praia", "labels": {"a": "Cozinha"}})
        self.assertEqual(status, 200)
        self.assertEqual(res["device"]["name"], "Casa da praia")
        self.assertEqual(res["device"]["labels"]["a"], "Cozinha")
        self.assertEqual(self.post_json("/api/devices/naoexiste", {"name": "x"})[0], 404)
        self.assertEqual(self.request("DELETE", "/api/devices/1")[0], 200)
        self.assertEqual([d["id"] for d in self.get_json("/api/devices")["devices"]], ["casa"])

    def test_07_exportacoes(self):
        now = int(time.time())
        status, h, data = self.request("GET", "/api/export.csv?device=casa&from=%d" % (now - 3600))
        self.assertEqual(status, 200)
        self.assertIn("attachment", h["Content-Disposition"])
        lines = data.decode("utf-8-sig").strip().split("\r\n")
        self.assertEqual(len(lines), 8)
        self.assertTrue(lines[0].startswith("data_hora;epoch;pa;pb"))
        self.assertIn(";410,2;", lines[1])
        status, _h, data = self.request("GET", "/api/export.csv?device=casa&kind=hora&fmt=en&from=%d" % (now - 3600))
        self.assertIn(",0.12,", data.decode("utf-8-sig"))
        status, h, data = self.request("GET", "/api/backup")
        self.assertEqual(status, 200)
        self.assertEqual(data[:15], b"SQLite format 3")
        path = os.path.join(self.tmp.name, "copia.db")
        with open(path, "wb") as fh:
            fh.write(data)
        db = sqlite3.connect(path)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM readings WHERE device_id = 'casa'").fetchone()[0], 7)
        db.close()

    def test_08_arquivos_estaticos(self):
        status, h, data = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", h["Content-Type"])
        self.assertIn(b"Painel de Energia", data)
        etag = h["ETag"]
        self.assertEqual(self.request("GET", "/", headers={"If-None-Match": etag})[0], 304)
        status, h, data = self.request("GET", "/vendor/echarts.min.js", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(h.get("Content-Encoding"), "gzip")
        for bad in ("/../config.py", "/..%2f..%2fapp%2fconfig.py", "/css/../../config.py", "/naoexiste.js"):
            self.assertEqual(self.request("GET", bad)[0], 404, bad)
        self.assertEqual(self.request("DELETE", "/")[0], 405)


class TokenAndAuthTest(ServerCase):
    ENV = {"INGEST_TOKEN": "s3gredo", "DASH_USER": "pedro", "DASH_PASSWORD": "senha-do-painel"}

    def auth(self, user="pedro", pwd="senha-do-painel"):
        return {"Authorization": "Basic " + base64.b64encode(("%s:%s" % (user, pwd)).encode()).decode()}

    def test_token_de_ingestao(self):
        body = json.dumps(meter(0)).encode()
        self.assertEqual(self.request("POST", "/api/ingest", body)[0], 403)
        self.assertEqual(self.request("POST", "/api/ingest/errado", body)[0], 403)
        self.assertEqual(self.request("POST", "/outra/rota", body)[0], 403)
        self.assertEqual(self.request("POST", "/api/ingest/s3gredo", body)[0], 200)
        time.sleep(1.1)
        self.assertEqual(self.request("GET", "/api/ingest?token=s3gredo&" + urlencode(meter(1)))[0], 200)
        st = self.get_json("/api/status", self.auth())
        self.assertEqual(st["ingest"]["path"], "/api/ingest/s3gredo")
        self.assertEqual(st["devices"][0]["n_readings"], 2)

    def test_login_do_painel(self):
        status, h, _d = self.request("GET", "/api/status")
        self.assertEqual(status, 401)
        self.assertIn("Basic", h["WWW-Authenticate"])
        self.assertEqual(self.request("GET", "/")[0], 401)
        self.assertEqual(self.request("GET", "/api/status", headers=self.auth(pwd="errada"))[0], 401)
        self.assertEqual(self.request("GET", "/api/status", headers=self.auth())[0], 200)
        self.assertEqual(self.request("GET", "/", headers=self.auth())[0], 200)
        self.assertEqual(self.request("GET", "/api/health")[0], 200)     # usado pelo healthcheck do Docker
        self.assertEqual(self.post_json("/api/settings", {"tariff": 1})[0], 401)


if __name__ == "__main__":
    unittest.main()
