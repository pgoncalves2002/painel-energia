"""Confere o pacote da integração do Home Assistant (sem precisar do Home Assistant instalado)."""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "custom_components", "painel_energia")


class HaPackageTest(unittest.TestCase):
    def test_copias_do_nucleo_e_da_interface_estao_em_dia(self):
        res = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "sync_ha.py"), "--check"],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_manifesto_e_traducoes(self):
        manifest = json.load(open(os.path.join(PKG, "manifest.json"), encoding="utf-8"))
        self.assertEqual(manifest["domain"], "painel_energia")
        self.assertTrue(manifest["config_flow"])
        self.assertEqual(manifest["requirements"], [])          # nada a instalar: só a biblioteca padrão
        keys = None
        for lang in ("pt-BR", "pt", "en"):
            data = json.load(open(os.path.join(PKG, "translations", lang + ".json"), encoding="utf-8"))
            self.assertIn("{path}", data["config"]["create_entry"]["default"])
            cur = sorted(data["options"]["step"]["init"]["data"])
            keys = keys or cur
            self.assertEqual(cur, keys)
            self.assertEqual(sorted(data["entity"]["sensor"]["situacao_tensao"]["state"]),
                             ["adequada", "ausente", "critica", "precaria"])

    def test_nucleo_copiado_funciona_como_pacote_proprio(self):
        # importa só a pasta core (sem o Home Assistant) e grava uma leitura
        code = (
            "import sys, tempfile, os, json; sys.path.insert(0, %r)\n"
            "from core.service import Core; from core.store import Store; from core.timeutil import Clock\n"
            "from core.ha_discovery import entities, state_payload\n"
            "d = tempfile.mkdtemp(); s = Store(os.path.join(d, 'e.db'), Clock('America/Sao_Paulo')); c = Core(s)\n"
            "r = c.ingest_raw(json.dumps({'id': '1', 'pa': '10', 'pb': '5', 'pc': '1', 'uarms': '127.0'}).encode(), '', 'http')\n"
            "p = state_payload(s.snapshot('1'))\n"
            "assert r['ok'] and p['pt'] == 16.0 and p['sit_ua'] == 'adequada', (r, p)\n"
            "assert c.api_get('/api/live', {'device': '1'})['voltage']['a'] == 'adequada'\n"
            "s.close(); print(len(entities(3)), len(entities(1)))\n" % PKG)
        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.split(), ["45", "15"])


if __name__ == "__main__":
    unittest.main()
