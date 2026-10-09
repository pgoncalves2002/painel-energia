"""Partida do add-on do Home Assistant: opções viram a configuração do painel."""
import importlib.util
import os
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("addon_run", os.path.join(ROOT, "painel_energia_addon", "run.py"))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


class AddonRunTest(unittest.TestCase):
    def env(self, options, answers):
        with mock.patch.object(run, "supervisor", side_effect=lambda path: answers.get(path)), \
                mock.patch.object(run, "log"):
            return run.build_env(options, {})

    def test_broker_do_home_assistant_e_endereco_do_medidor(self):
        env = self.env({"tarifa_kwh": 1.02, "unidade_contadores": "wh", "demonstracao": "solar"}, {
            "/services/mqtt": {"host": "core-mosquitto", "port": 1883, "username": "addons", "password": "segredo"},
            "/addons/self/info": {"network": {"8080/tcp": 8090}},
            "/network/info": {"interfaces": [{"primary": False, "ipv4": {"address": ["10.0.0.5/24"]}},
                                             {"primary": True, "ipv4": {"address": ["192.168.0.102/24"]}}]},
            "/info": {"timezone": "America/Sao_Paulo"}})
        self.assertEqual((env["MQTT_HOST"], env["MQTT_USERNAME"], env["MQTT_PASSWORD"]), ("core-mosquitto", "addons", "segredo"))
        self.assertEqual((env["PUBLIC_HOST"], env["PUBLIC_INGEST_PORT"]), ("192.168.0.102", "8090"))
        self.assertEqual((env["ADDON"], env["HTTP_PORT"], env["INGEST_PORT"], env["DATA_DIR"]), ("1", "8099", "8080", "/data"))
        self.assertEqual((env["TARIFA_KWH"], env["COUNTER_UNIT"], env["DEMO"], env["TZ"]), ("1.02", "wh", "solar", "America/Sao_Paulo"))

    def test_sem_supervisor_e_sem_broker_o_painel_sobe_assim_mesmo(self):
        env = self.env({}, {})
        self.assertEqual((env["MQTT_HOST"], env["DEMO"], env["PUBLIC_INGEST_PORT"]), ("", "0", "8080"))
        self.assertNotIn("PUBLIC_HOST", env)

    def test_broker_informado_nas_opcoes_tem_prioridade(self):
        env = self.env({"mqtt_host": "192.168.0.50", "mqtt_usuario": "u", "mqtt_senha": "s"},
                       {"/services/mqtt": {"host": "core-mosquitto"}})
        self.assertEqual((env["MQTT_HOST"], env["MQTT_PORT"], env["MQTT_USERNAME"]), ("192.168.0.50", "1883", "u"))

    def test_config_do_addon(self):
        text = open(os.path.join(ROOT, "painel_energia_addon", "config.yaml"), encoding="utf-8").read()
        for needle in ("ingress: true", "ingress_port: 8099", "8080/tcp: 8080", "mqtt:want", "init: false"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
