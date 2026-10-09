"""Testes da ponte MQTT e da descoberta do Home Assistant.

Os testes de ponta a ponta precisam do broker `mosquitto` instalado na máquina
(sudo apt install mosquitto / brew install mosquitto); sem ele são pulados.
"""
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest

from app import ha_discovery
from app.config import Config
from app.mqtt_bridge import HAVE_PAHO, topic_matches
from app.server import App
from tests.test_api import meter

MOSQUITTO = shutil.which("mosquitto") or (os.path.exists("/usr/sbin/mosquitto") and "/usr/sbin/mosquitto") or None
MOSQUITTO_PASSWD = shutil.which("mosquitto_passwd")
ENTRYPOINT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mosquitto", "entrypoint.sh")


class DiscoveryTest(unittest.TestCase):
    DEVICE = {"id": "Casa 01", "name": "Medidor da casa", "model": "SM-3W Lite", "phases": 3}

    def test_entidades_trifasico(self):
        ents = ha_discovery.entities(3)
        keys = [e["key"] for e in ents]
        self.assertEqual(len(keys), len(set(keys)))
        for k in ("pt", "pa", "pb", "pc", "uarms", "ubrms", "ucrms", "iarms", "itrms", "freq", "e_c_t", "e_g_t",
                  "hoje_c", "e_c_a", "tpsd"):
            self.assertIn(k, keys)
        by = {e["key"]: e for e in ents}
        self.assertEqual((by["pt"]["device_class"], by["pt"]["unit"], by["pt"]["state_class"]),
                         ("power", "W", "measurement"))
        self.assertEqual((by["e_c_t"]["device_class"], by["e_c_t"]["unit"], by["e_c_t"]["state_class"]),
                         ("energy", "kWh", "total_increasing"))
        self.assertFalse(by["pga"]["enabled"])
        self.assertTrue(by["tpsd"]["diagnostic"])

    def test_entidades_monofasico(self):
        keys = [e["key"] for e in ha_discovery.entities(1)]
        self.assertIn("uarms", keys)
        self.assertNotIn("ubrms", keys)
        self.assertNotIn("pb", keys)
        self.assertNotIn("e_c_a", keys)

    def test_mensagens_de_descoberta(self):
        msgs = ha_discovery.discovery_messages("homeassistant", "painel-energia", self.DEVICE,
                                               "http://192.168.0.10:8080")
        self.assertEqual(len(msgs), len(ha_discovery.entities(3)))
        topics = [t for t, _ in msgs]
        self.assertIn("homeassistant/sensor/painel_energia_casa_01/pt/config", topics)
        payload = dict(msgs)["homeassistant/sensor/painel_energia_casa_01/pt/config"]
        self.assertEqual(payload["unique_id"], "painel_energia_casa_01_pt")
        self.assertEqual(payload["state_topic"], "painel-energia/casa_01/state")
        self.assertEqual(payload["value_template"], "{{ value_json.pt | default(none) }}")
        self.assertEqual(payload["device"]["identifiers"], ["painel_energia_casa_01"])
        self.assertEqual(payload["device"]["manufacturer"], "IE Tecnologia")
        self.assertEqual(payload["device"]["configuration_url"], "http://192.168.0.10:8080")
        self.assertEqual([a["topic"] for a in payload["availability"]],
                         ["painel-energia/bridge/status", "painel-energia/casa_01/availability"])
        # chaves que versões do Home Assistant rejeitam não podem aparecer
        for p in dict(msgs).values():
            self.assertNotIn("object_id", p)
            json.dumps(p)
        self.assertTrue(set(topics) <= set(ha_discovery.discovery_topics("homeassistant", "Casa 01")))

    def test_estado(self):
        snap = {"ts": 1, "device": self.DEVICE,
                "reading": {"pt": 780.66, "pa": 410.24, "uarms": 127.314, "pft": -0.975, "pfa": None,
                            "freq": 60.012, "ept_c": 35.02, "ept_g": 0.0, "tpsd": 30.44},
                "totals": {"c_t": 12.34567, "g_t": 0.0, "c_a": 1.0},
                "today": {"c_t": 3.21099, "g_t": 0.0}}
        st = ha_discovery.state_payload(snap)
        self.assertEqual(st["pt"], 780.7)
        self.assertEqual(st["uarms"], 127.31)
        self.assertEqual(st["pft_pct"], 97.5)
        self.assertIsNone(st["pfa_pct"])
        self.assertEqual(st["e_c_t"], 12.346)
        self.assertEqual(st["hoje_c"], 3.211)
        self.assertEqual(st["medidor_ept_c"], 35.02)
        self.assertNotIn("ept_c", st)
        json.dumps(st)

    def test_filtro_de_topico(self):
        self.assertTrue(topic_matches("medidor/energia", "medidor/energia"))
        self.assertTrue(topic_matches("medidor/#", "medidor/a/b"))
        self.assertTrue(topic_matches("medidor/+/dados", "medidor/x/dados"))
        self.assertFalse(topic_matches("medidor/energia", "medidor/energia/x"))
        self.assertFalse(topic_matches("medidor/+", "medidor"))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for(cond, timeout=8.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


@unittest.skipUnless(MOSQUITTO and HAVE_PAHO, "requer o broker mosquitto e a biblioteca paho-mqtt")
class MqttEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import paho.mqtt.client as mqtt
        cls.mqtt = mqtt
        cls.tmp = tempfile.TemporaryDirectory()
        cls.port = free_port()
        conf = os.path.join(cls.tmp.name, "mosquitto.conf")
        os.chmod(cls.tmp.name, 0o755)
        with open(conf, "w") as fh:
            fh.write("listener %d 127.0.0.1\nallow_anonymous true\npersistence false\n" % cls.port)
        os.chmod(conf, 0o644)
        cls.broker = subprocess.Popen([MOSQUITTO, "-c", conf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def up():
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.3).close()
                return True
            except OSError:
                return False
        if not wait_for(up):
            raise RuntimeError("mosquitto não iniciou")

        # espião: guarda tudo o que passa pelo broker
        cls.seen = []
        cls.lock = threading.Lock()
        spy = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="espiao")

        def on_message(_c, _u, m):
            with cls.lock:
                cls.seen.append((m.topic, bytes(m.payload), bool(m.retain)))
        spy.on_message = on_message
        spy.on_connect = lambda c, *a: c.subscribe("#", qos=1)
        spy.connect("127.0.0.1", cls.port)
        spy.loop_start()
        cls.spy = spy

        data = os.path.join(cls.tmp.name, "data")
        cls.cfg = Config.from_env({
            "DATA_DIR": data, "HTTP_PORT": "0", "TZ": "America/Sao_Paulo",
            "MQTT_HOST": "127.0.0.1", "MQTT_PORT": str(cls.port), "MQTT_TOPIC_IN": "medidor/energia, outro/+/dados",
            "PUBLIC_URL": "http://192.168.0.10:8080/",
        })
        cls.app = App(cls.cfg)
        cls.app.start()
        if not wait_for(lambda: cls.app.mqtt.connected):
            raise RuntimeError("a ponte não conectou ao broker")

    @classmethod
    def tearDownClass(cls):
        cls.app.stop()
        cls.spy.loop_stop()
        cls.spy.disconnect()
        cls.broker.terminate()
        cls.broker.wait(timeout=5)
        cls.app.store.close_thread()
        cls.tmp.cleanup()

    def topics(self, prefix=""):
        with self.lock:
            return [(t, p, r) for (t, p, r) in self.seen if t.startswith(prefix)]

    def last(self, topic):
        items = [p for (t, p, _r) in self.topics() if t == topic]
        return items[-1] if items else None

    def publish_meter(self, i, topic="medidor/energia", **over):
        info = self.spy.publish(topic, json.dumps(meter(i, dev="Casa 01", **over)), qos=1)
        info.wait_for_publish(timeout=3)

    def test_fluxo_completo(self):
        n_entities = len(ha_discovery.entities(3))
        self.assertTrue(wait_for(lambda: self.last("painel-energia/bridge/status") == b"online"))

        # 1) o medidor publica; a ponte grava, anuncia ao HA e publica o estado
        self.publish_meter(0)
        self.assertTrue(wait_for(lambda: self.app.store.state("Casa_01") is not None))
        self.assertTrue(wait_for(lambda: len(self.topics("homeassistant/sensor/")) >= n_entities))
        configs = {t: json.loads(p) for (t, p, _r) in self.topics("homeassistant/sensor/")}
        self.assertEqual(len(configs), n_entities)
        cfg_pt = configs["homeassistant/sensor/painel_energia_casa_01/pt/config"]
        self.assertEqual(cfg_pt["state_topic"], "painel-energia/casa_01/state")
        self.assertEqual(cfg_pt["device"]["configuration_url"], "http://192.168.0.10:8080")
        self.assertTrue(wait_for(lambda: self.last("painel-energia/casa_01/state") is not None))
        state = json.loads(self.last("painel-energia/casa_01/state"))
        self.assertEqual(state["pt"], 780.7)
        self.assertEqual(state["uarms"], 127.31)
        self.assertEqual(state["e_c_t"], 0.0)
        self.assertEqual(self.last("painel-energia/casa_01/availability"), b"online")

        # 2) segunda leitura: os totais de energia avançam
        time.sleep(1.1)
        self.publish_meter(3)
        self.assertTrue(wait_for(lambda: json.loads(self.last("painel-energia/casa_01/state"))["e_c_t"] > 0))
        state = json.loads(self.last("painel-energia/casa_01/state"))
        self.assertAlmostEqual(state["e_c_t"], 0.06, places=3)
        self.assertAlmostEqual(state["hoje_c"], 0.06, places=3)
        self.assertAlmostEqual(state["e_c_a"], 0.03, places=3)
        diag = self.app.diag.dump()
        self.assertEqual(diag["by_source"], {"mqtt": 2})
        self.assertEqual(diag["recent"][0]["topic"], "medidor/energia")

        # 3) filtro com curinga também funciona
        time.sleep(1.1)
        self.publish_meter(4, topic="outro/sala/dados")
        self.assertTrue(wait_for(lambda: self.app.store.state("Casa_01").n == 3))

        # 4) lixo no tópico do medidor não derruba nada; mensagens da própria ponte são ignoradas
        rx = self.app.mqtt.rx_count
        self.spy.publish("medidor/energia", "isto nao e json", qos=1).wait_for_publish(timeout=3)
        self.assertTrue(wait_for(lambda: self.app.diag.dump()["failed"] == 1))
        self.spy.publish("painel-energia/casa_01/state", json.dumps(meter(9)), qos=1).wait_for_publish(timeout=3)
        time.sleep(0.4)
        self.assertEqual(self.app.mqtt.rx_count, rx + 1)
        self.assertEqual(self.app.store.state("Casa_01").n, 3)

        # 5) o Home Assistant reinicia e avisa: a descoberta é reenviada
        before = len(self.topics("homeassistant/sensor/"))
        self.spy.publish("homeassistant/status", "online", qos=1).wait_for_publish(timeout=3)
        self.assertTrue(wait_for(lambda: len(self.topics("homeassistant/sensor/")) >= before + n_entities))

        # 6) medidor para de enviar: fica indisponível no HA
        self.app.store.check_online(now=time.time() + 3600)
        self.assertTrue(wait_for(lambda: self.last("painel-energia/casa_01/availability") == b"offline"))
        time.sleep(1.1)
        self.publish_meter(6)
        self.assertTrue(wait_for(lambda: self.last("painel-energia/casa_01/availability") == b"online"))

        # 7) renomear atualiza o dispositivo no HA; excluir remove os sensores
        self.app.store.update_device("Casa_01", name="Quadro geral")
        self.assertTrue(wait_for(lambda: json.loads(self.last(
            "homeassistant/sensor/painel_energia_casa_01/pt/config") or b"{}").get("device", {}).get("name")
            == "Quadro geral"))
        self.app.store.delete_device("Casa_01")
        self.assertTrue(wait_for(lambda: self.last("homeassistant/sensor/painel_energia_casa_01/pt/config") == b""))
        self.assertEqual(self.last("homeassistant/sensor/painel_energia_casa_01/e_c_t/config"), b"")

        status = self.app.mqtt.status()
        self.assertTrue(status["connected"])
        self.assertGreater(status["tx"], 2 * n_entities)


@unittest.skipUnless(MOSQUITTO and MOSQUITTO_PASSWD and HAVE_PAHO and os.name == "posix",
                     "requer mosquitto, mosquitto_passwd e a biblioteca paho-mqtt")
class BrokerEntrypointTest(unittest.TestCase):
    """O broker que acompanha o painel, iniciado pelo mesmo script usado no docker-compose."""

    def start_broker(self, **env):
        tmp = tempfile.mkdtemp(prefix="painel-mosquitto-")
        self.addCleanup(shutil.rmtree, tmp, True)
        os.chmod(tmp, 0o755)
        port = free_port()
        e = dict(os.environ)
        e["PATH"] = e.get("PATH", "") + os.pathsep + os.path.dirname(MOSQUITTO)
        e.update({"MOSQUITTO_DATA_DIR": os.path.join(tmp, "data"), "MOSQUITTO_CONF": os.path.join(tmp, "mosquitto.conf"),
                  "MOSQUITTO_PORT": str(port)})
        e.update(env)
        proc = subprocess.Popen(["/bin/sh", ENTRYPOINT], env=e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop():
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.addCleanup(stop)

        def up():
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
                return True
            except OSError:
                return False
        self.assertTrue(wait_for(up), "o broker não iniciou")
        return port

    def connect(self, port, client_id, user=None, password=None):
        """Devolve (cliente, conectou?, tópicos recebidos)."""
        import paho.mqtt.client as mqtt
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        if user:
            c.username_pw_set(user, password)
        done = threading.Event()
        result = {}
        seen = []

        def on_connect(_c, _u, _flags, reason, _props=None):
            result["ok"] = not reason.is_failure
            done.set()
        c.on_connect = on_connect
        c.on_message = lambda _c, _u, m: seen.append(m.topic)
        c.connect("127.0.0.1", port)
        c.loop_start()
        self.addCleanup(c.loop_stop)
        self.addCleanup(c.disconnect)
        done.wait(5)
        return c, bool(result.get("ok")), seen

    def test_medidor_sem_login_so_envia_leituras(self):
        port = self.start_broker(MQTT_USERNAME="painel", MQTT_PASSWORD="s3nha com espaço",
                                 MQTT_TOPIC_IN="medidor/energia, casa/+/leitura")
        painel, ok, seen = self.connect(port, "painel", "painel", "s3nha com espaço")
        self.assertTrue(ok)
        painel.subscribe("#", qos=1)
        medidor, ok, medidor_seen = self.connect(port, "medidor")          # sem usuário e senha
        self.assertTrue(ok)
        medidor.subscribe("#", qos=1)                                      # tentativa de leitura: negada
        time.sleep(0.4)
        for topic in ("medidor/energia", "casa/sala/leitura", "homeassistant/sensor/x/config", "painel-energia/x/state"):
            medidor.publish(topic, "{}", qos=1).wait_for_publish(timeout=3)
        painel.publish("painel-energia/casa/state", "{}", qos=1).wait_for_publish(timeout=3)
        self.assertTrue(wait_for(lambda: "painel-energia/casa/state" in seen))
        time.sleep(0.3)
        self.assertEqual(sorted(seen), ["casa/sala/leitura", "medidor/energia", "painel-energia/casa/state"])
        self.assertEqual(medidor_seen, [])
        _c, ok, _seen = self.connect(port, "intruso", "painel", "senha errada")
        self.assertFalse(ok)

    def test_login_obrigatorio(self):
        port = self.start_broker(MQTT_USERNAME="painel", MQTT_PASSWORD="segredo", MQTT_METER_ANONYMOUS="false")
        _c, ok, _seen = self.connect(port, "medidor")
        self.assertFalse(ok)
        _c, ok, _seen = self.connect(port, "painel", "painel", "segredo")
        self.assertTrue(ok)

    def test_sem_senha_o_broker_fica_aberto(self):
        port = self.start_broker(MQTT_USERNAME="", MQTT_PASSWORD="")
        a, ok, seen = self.connect(port, "a")
        self.assertTrue(ok)
        a.subscribe("#", qos=1)
        time.sleep(0.3)
        b, ok, _seen = self.connect(port, "b")
        self.assertTrue(ok)
        b.publish("qualquer/topico", "x", qos=1).wait_for_publish(timeout=3)
        self.assertTrue(wait_for(lambda: seen == ["qualquer/topico"]))


if __name__ == "__main__":
    unittest.main()
