import json
import unittest
from urllib.parse import quote, urlencode

from app.parser import ParseError, parse_message, sanitize_id, to_float

# Mensagem real documentada do SM-3W Lite (40 campos, valores como texto).
SM3W = {
    "id": "12345678901234567890123", "pa": "0.00", "pb": "0.00", "pc": "0.00", "pt": "0.00",
    "qa": "0.00", "qb": "0.00", "qc": "0.00", "qt": "0.00", "sa": "0.00", "sb": "0.00", "sc": "0.00",
    "st": "0.00", "uarms": "120.15", "ubrms": "0.31", "ucrms": "0.29", "iarms": "0.00", "ibrms": "0.00",
    "icrms": "0.00", "itrms": "0.00", "pfa": "1.00", "pfb": "1.00", "pfc": "1.00", "pft": "1.00",
    "pga": "-90.02", "pgb": "0.00", "pgc": "-90.02", "freq": "60.00", "epa_c": "0.00", "epb_c": "0.00",
    "epc_c": "0.00", "ept_c": "0.00", "epa_g": "0.00", "epb_g": "0.00", "epc_g": "0.00", "ept_g": "0.00",
    "yuaub": "269.97", "yuauc": "269.97", "yubuc": "0.00", "tpsd": "30.08",
}

# Mensagem documentada do SM-W Lite (monofásico, 13 campos).
SMW = {
    "id": "1", "pa": "-0.03", "qa": "-0.02", "sa": "0.03", "uarms": "125.80", "iarms": "0.00",
    "pft": "1.00", "pga": "-89.99", "freq": "60.01", "epa_c": "0.00", "epa_g": "0.00",
    "tpsd": "30.80", "rssi_wifi": "-64.00",
}


class ParserTest(unittest.TestCase):
    def test_json_puro_trifasico(self):
        r = parse_message(json.dumps(SM3W).encode())
        self.assertEqual(r["id"], "12345678901234567890123")
        self.assertEqual(r["_phases"], 3)
        self.assertAlmostEqual(r["uarms"], 120.15)
        self.assertAlmostEqual(r["pga"], -90.02)
        self.assertAlmostEqual(r["tpsd"], 30.08)
        self.assertEqual(r["ept_c"], 0.0)
        self.assertEqual(r["_extra"], {})

    def test_mensagens_reais_publicadas_por_usuarios(self):
        # SM-W Lite com geração solar (fórum Home Assistant Brasil): potência e fator de potência negativos
        mono = {"id": "1", "pa": "-956.07", "qa": "5.79", "sa": "956.08", "uarms": "225.49", "iarms": "4.27",
                "pft": "-0.99", "pga": "178.67", "freq": "59.97", "epa_c": "30.79", "epa_g": "49.00", "tpsd": "40.24"}
        r = parse_message(json.dumps(mono))
        self.assertEqual(r["_phases"], 1)
        self.assertAlmostEqual(r["pt"], -956.07)
        self.assertAlmostEqual(r["ept_c"], 30.79)
        self.assertAlmostEqual(r["ept_g"], 49.00)
        self.assertIsNone(r.get("rssi_wifi"))                # este firmware não envia o sinal do Wi-Fi
        # SM-3W Lite com uma fase injetando e duas consumindo (trecho da mensagem)
        tri = {"pa": "-3778.44", "pb": "331.22", "pc": "36.13", "pt": "-3411.08", "ept_g": "6.32", "ept_c": "4.82"}
        r = parse_message(json.dumps(tri))
        self.assertEqual(r["_phases"], 3)
        self.assertAlmostEqual(r["pt"], -3411.08)
        self.assertAlmostEqual(r["pa"], -3778.44)
        self.assertEqual(r["id"], sanitize_id(None))         # sem id na mensagem: usa o id padrão

    def test_contadores_em_wh_viram_kwh(self):
        msg = dict(SM3W, epa_c="12340.00", ept_c="35020.00", ept_g="150.00", pt="780.70")
        r = parse_message(json.dumps(msg), counter_scale=0.001)
        self.assertAlmostEqual(r["ept_c"], 35.02)
        self.assertAlmostEqual(r["epa_c"], 12.34)
        self.assertAlmostEqual(r["ept_g"], 0.15)
        self.assertAlmostEqual(r["pt"], 780.70)            # só os contadores mudam
        self.assertAlmostEqual(parse_message(json.dumps(msg))["ept_c"], 35020.0)

    def test_monofasico_preenche_totais(self):
        r = parse_message(json.dumps(SMW))
        self.assertEqual(r["_phases"], 1)
        self.assertAlmostEqual(r["pt"], -0.03)
        self.assertAlmostEqual(r["st"], 0.03)
        self.assertAlmostEqual(r["pfa"], 1.0)
        self.assertEqual(r["ept_c"], 0.0)
        self.assertAlmostEqual(r["rssi_wifi"], -64.0)

    def test_json_como_chave_de_formulario(self):
        # alguns firmwares enviam o JSON com Content-Type de formulário
        body = quote(json.dumps(SM3W)) + "="
        r = parse_message(body.encode())
        self.assertAlmostEqual(r["uarms"], 120.15)
        r2 = parse_message((json.dumps(SM3W) + "=").encode())
        self.assertAlmostEqual(r2["uarms"], 120.15)

    def test_json_como_valor_de_formulario(self):
        body = urlencode({"data": json.dumps(SMW)})
        self.assertAlmostEqual(parse_message(body)["uarms"], 125.80)

    def test_json_url_encoded_sem_chave(self):
        self.assertAlmostEqual(parse_message(quote(json.dumps(SMW)))["uarms"], 125.80)

    def test_get_com_pares(self):
        r = parse_message(b"", query=urlencode(SM3W))
        self.assertEqual(r["_phases"], 3)
        self.assertAlmostEqual(r["yuaub"], 269.97)

    def test_get_com_json_em_parametro(self):
        r = parse_message(b"", query="json=" + quote(json.dumps(SMW)))
        self.assertAlmostEqual(r["freq"], 60.01)

    def test_envelope(self):
        r = parse_message(json.dumps({"id": "abc", "payload": {"pa": 10, "uarms": 127.1}}))
        self.assertEqual(r["id"], "abc")
        self.assertEqual(r["pa"], 10.0)
        r = parse_message(json.dumps([{"variable": "payload", "value": json.dumps(SMW)}]))
        self.assertAlmostEqual(r["uarms"], 125.80)

    def test_chaves_maiusculas_numeros_e_virgula(self):
        r = parse_message(json.dumps({"ID": 7, "PA": 12.5, "PB": "3,25", "UARMS": "127,4", "novo": "x"}))
        self.assertEqual(r["id"], "7")
        self.assertEqual(r["pa"], 12.5)
        self.assertEqual(r["pb"], 3.25)
        self.assertAlmostEqual(r["uarms"], 127.4)
        self.assertAlmostEqual(r["pt"], 15.75)          # total derivado das fases
        self.assertEqual(r["_extra"], {"novo": "x"})

    def test_valores_invalidos_viram_none(self):
        r = parse_message(json.dumps({"id": "1", "pa": "nan", "pb": "", "uarms": "127.0", "pt": "abc"}))
        self.assertIsNone(r["pa"])
        self.assertIsNone(r["pb"])
        self.assertEqual(r["uarms"], 127.0)

    def test_rejeita_lixo(self):
        for body in (b"", b"oi", b"{}", b'{"foo": 1}', b"a=1&b=2", b"[1,2,3]", b'{"pa": "x"}'):
            with self.assertRaises(ParseError):
                parse_message(body)

    def test_auxiliares(self):
        self.assertEqual(sanitize_id("medidor casa/01"), "medidor_casa_01")
        self.assertEqual(sanitize_id(""), "1")
        self.assertEqual(sanitize_id(None), "1")
        self.assertIsNone(to_float(True))
        self.assertIsNone(to_float("inf"))
        self.assertEqual(to_float(" -12.50 "), -12.5)


if __name__ == "__main__":
    unittest.main()
