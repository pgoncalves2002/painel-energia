import os
import tempfile
import unittest
from datetime import datetime

from app.parser import normalize
from app.store import Store
from app.timeutil import Clock

TZ = "America/Sao_Paulo"


def local_epoch(clock, y, m, d, hh=0, mm=0, ss=0):
    return int(datetime(y, m, d, hh, mm, ss, tzinfo=clock.tz).timestamp())


def reading(dev="1", p=(300.0, 200.0, 100.0), u=(127.0, 126.5, 127.8), ec=None, eg=None, **extra):
    """Leitura trifásica normalizada. ec/eg = contadores (a, b, c, t) consumidos/gerados."""
    low = {"id": dev, "pa": p[0], "pb": p[1], "pc": p[2], "pt": sum(p),
           "uarms": u[0], "ubrms": u[1], "ucrms": u[2],
           "iarms": abs(p[0]) / u[0] if u[0] else 0, "ibrms": abs(p[1]) / u[1] if u[1] else 0,
           "icrms": abs(p[2]) / u[2] if u[2] else 0, "freq": 60.0, "pft": 0.97, "tpsd": 31.0}
    if ec is not None:
        low.update({"epa_c": ec[0], "epb_c": ec[1], "epc_c": ec[2], "ept_c": ec[3]})
    if eg is not None:
        low.update({"epa_g": eg[0], "epb_g": eg[1], "epc_g": eg[2], "ept_g": eg[3]})
    low.update(extra)
    return normalize(low)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        self.clock = Clock(TZ)
        self.store = Store(self.path, self.clock)

    def tearDown(self):
        self.store.close_thread()
        self.tmp.cleanup()

    def reopen(self):
        self.store.close_thread()
        self.store = Store(self.path, self.clock)
        return self.store

    # ------------------------------------------------------------------
    def test_energia_por_diferenca_de_contador(self):
        t0 = local_epoch(self.clock, 2026, 10, 7, 10, 0, 10)
        s = self.store
        s.ingest(reading(ec=(10.00, 20.00, 30.00, 60.00), eg=(0, 0, 0, 0)), ts=t0)
        s.ingest(reading(ec=(10.01, 20.02, 30.03, 60.06), eg=(0, 0, 0, 0)), ts=t0 + 30)
        s.ingest(reading(ec=(10.03, 20.03, 30.05, 60.11), eg=(0, 0, 0, 0)), ts=t0 + 60)
        e = s.energy_sum("1", t0 - 3600, t0 + 3600)
        self.assertAlmostEqual(e["c_a"], 0.03, places=6)
        self.assertAlmostEqual(e["c_b"], 0.03, places=6)
        self.assertAlmostEqual(e["c_c"], 0.05, places=6)
        self.assertAlmostEqual(e["c_t"], 0.11, places=6)
        self.assertEqual(e["g_t"], 0.0)
        self.assertFalse(e["est"])
        snap = s.snapshot("1")
        self.assertAlmostEqual(snap["totals"]["c_t"], 0.11, places=6)
        self.assertAlmostEqual(snap["today"]["c_t"], 0.11, places=6)
        self.assertEqual(snap["device"]["phases"], 3)
        self.assertEqual(snap["device"]["model"], "SM-3W Lite")
        self.assertEqual(snap["device"]["v_nom"], 127.0)

    def test_energia_repartida_entre_intervalos(self):
        base = local_epoch(self.clock, 2026, 10, 7, 10, 15, 0)   # limite de 15 min
        s = self.store
        s.ingest(reading(ec=(0, 0, 0, 100.0)), ts=base - 10)
        s.ingest(reading(ec=(0, 0, 0, 100.3)), ts=base + 20)
        rows = s.energy("1", base - 900, base + 900, "15min")["rows"]
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0]["c_t"], 0.1, places=6)
        self.assertAlmostEqual(rows[1]["c_t"], 0.2, places=6)

    def test_lacuna_e_marcada_como_estimada(self):
        t0 = local_epoch(self.clock, 2026, 10, 7, 8, 0, 0)
        s = self.store
        s.ingest(reading(ec=(0, 0, 0, 50.0)), ts=t0)
        s.ingest(reading(ec=(0, 0, 0, 50.01)), ts=t0 + 30)
        s.ingest(reading(ec=(0, 0, 0, 54.01)), ts=t0 + 30 + 4 * 3600)   # 4 h sem dados
        res = s.energy("1", t0, t0 + 5 * 3600, "hour")
        vals = [r["c_t"] for r in res["rows"]]
        self.assertAlmostEqual(sum(v for v in vals if v), 4.01, places=6)
        self.assertTrue(all(0.9 < v < 1.1 for v in vals[:4]))
        self.assertTrue(res["rows"][1]["est"])
        ev = [e for e in s.events("1") if e["kind"] == "offline"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["end_ts"] - ev[0]["ts"], 4 * 3600)

    def test_reinicio_e_salto_do_contador(self):
        t0 = local_epoch(self.clock, 2026, 10, 1, 0, 0, 0) - 60
        s = self.store
        s.ingest(reading(ec=(100.0, 100.0, 112.4, 312.40)), ts=t0)
        s.ingest(reading(ec=(100.01, 100.0, 112.4, 312.41)), ts=t0 + 30)
        r = s.ingest(reading(ec=(0.0, 0.0, 0.0, 0.00)), ts=t0 + 60)          # virou o mês: zerou
        self.assertTrue(any(f["status"] == "reinicio" for f in r["flags"]))
        s.ingest(reading(ec=(0.01, 0.0, 0.0, 0.02)), ts=t0 + 90)
        s.ingest(reading(ec=(0.02, 0.0, 0.0, 0.05)), ts=t0 + 120)
        e = s.energy_sum("1", t0 - 900, t0 + 900)
        self.assertAlmostEqual(e["c_t"], 0.01 + 0.02 + 0.03, places=6)
        self.assertAlmostEqual(e["c_a"], 0.01 + 0.01 + 0.01, places=6)
        self.assertAlmostEqual(s.snapshot("1")["totals"]["c_t"], 0.06, places=6)   # total próprio não zera
        self.assertTrue(any(ev["kind"] == "contador" for ev in s.events("1")))

    def test_contador_que_salta_sempre_nao_enche_as_ocorrencias(self):
        t0 = local_epoch(self.clock, 2026, 10, 15, 12, 0, 0)
        s = self.store
        for i in range(40):                                                  # 20 min de saltos impossíveis
            s.ingest(reading(ec=(0, 0, 0, 1000.0 + 5.0 * i)), ts=t0 + 30 * i)
        self.assertEqual(len([e for e in s.events("1") if e["kind"] == "contador"]), 1)
        s.ingest(reading(ec=(0, 0, 0, 5000.0)), ts=t0 + 2 * 3600)
        self.assertEqual(len([e for e in s.events("1") if e["kind"] == "contador"]), 2)
        self.assertEqual(s.snapshot("1")["totals"]["c_t"], 0.0)

    def test_leitura_corrompida_nao_vira_pico(self):
        t0 = local_epoch(self.clock, 2026, 10, 15, 12, 0, 0)
        s = self.store
        s.ingest(reading(ec=(50, 50, 50, 150.00)), ts=t0)
        s.ingest(reading(ec=(50, 50, 50, 150.02)), ts=t0 + 30)
        r = s.ingest(reading(ec=(50, 50, 50, 9999.0)), ts=t0 + 60)           # valor absurdo
        self.assertEqual(r["flags"][0]["status"], "salto")
        s.ingest(reading(ec=(50, 50, 50, 150.05)), ts=t0 + 90)               # volta ao normal
        s.ingest(reading(ec=(50, 50, 50, 150.07)), ts=t0 + 120)
        e = s.energy_sum("1", t0 - 900, t0 + 900)
        # perde-se só o trecho em volta da leitura ruim (0,03 kWh); nenhum pico é criado
        self.assertAlmostEqual(e["c_t"], 0.02 + 0.02, places=6)

    def test_geracao_e_modo_bidirecional(self):
        t0 = local_epoch(self.clock, 2026, 10, 7, 12, 0, 5)
        s = self.store
        s.ingest(reading(p=(-500, -400, -300), ec=(1, 1, 1, 3.0), eg=(2, 2, 2, 6.0)), ts=t0)
        s.ingest(reading(p=(-500, -400, -300), ec=(1, 1, 1, 3.0), eg=(2.01, 2.01, 2.0, 6.02)), ts=t0 + 60)
        e = s.energy_sum("1", t0 - 60, t0 + 120)
        self.assertEqual(e["c_t"], 0.0)
        self.assertAlmostEqual(e["g_t"], 0.02, places=6)
        self.assertAlmostEqual(e["g_a"], 0.01, places=6)

    def test_sem_contadores_integra_potencia(self):
        t0 = local_epoch(self.clock, 2026, 10, 7, 12, 0, 0)
        s = self.store
        s.ingest(reading(p=(1200, 600, 0)), ts=t0)
        s.ingest(reading(p=(1200, 600, 0)), ts=t0 + 60)
        e = s.energy_sum("1", t0, t0 + 900)
        self.assertAlmostEqual(e["c_t"], 1800 * 60 / 3.6e6, places=6)
        self.assertAlmostEqual(e["c_a"], 1200 * 60 / 3.6e6, places=6)
        self.assertTrue(e["est"])

    def test_intervalo_zero_e_diferente_de_sem_dados(self):
        t0 = local_epoch(self.clock, 2026, 10, 7, 3, 0, 0)
        s = self.store
        for i in range(4):
            s.ingest(reading(p=(0, 0, 0), ec=(5, 5, 5, 15.0)), ts=t0 + i * 30)
        rows = s.energy("1", t0, t0 + 3600, "15min")["rows"]
        self.assertEqual(rows[0]["c_t"], 0.0)      # houve leitura, consumo zero
        self.assertIsNone(rows[2]["c_t"])          # nenhuma leitura no intervalo

    def test_monofasico(self):
        t0 = local_epoch(self.clock, 2026, 10, 7, 9, 0, 0)
        s = self.store
        mono = lambda e: normalize({"id": "m1", "pa": 500.0, "uarms": 220.4, "iarms": 2.3, "pft": 0.98,
                                    "epa_c": e, "epa_g": 0.0})
        s.ingest(mono(7.00), ts=t0)
        s.ingest(mono(7.02), ts=t0 + 30)
        info = s.snapshot("m1")["device"]
        self.assertEqual((info["phases"], info["model"], info["v_nom"]), (1, "SM-W Lite", 220.0))
        e = s.energy_sum("m1", t0, t0 + 900)
        self.assertAlmostEqual(e["c_t"], 0.02, places=6)
        self.assertAlmostEqual(e["c_a"], 0.02, places=6)

    def test_estatisticas_15min_e_series(self):
        t0 = local_epoch(self.clock, 2026, 10, 7, 14, 0, 0)
        s = self.store
        for i in range(60):                       # 30 min de leituras a cada 30 s
            p = 1000.0 + (i % 2) * 200.0
            s.ingest(reading(p=(p, 0, 0), u=(127.0 + (i % 3), 126.0, 128.0)), ts=t0 + i * 30)
        rows = s.stat_rows("1", ["pt", "uarms"], t0, t0 + 1800)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["n"], 30)
        self.assertAlmostEqual(rows[0]["pt_avg"], 1100.0)
        self.assertEqual((rows[0]["pt_min"], rows[0]["pt_max"]), (1000.0, 1200.0))
        raw = s.series("1", ["pt", "uarms", "campo_invalido"], t0, t0 + 1800)
        self.assertEqual(raw["res"], 0)
        self.assertEqual(len(raw["t"]), 60)
        self.assertEqual(list(raw["series"].keys()), ["pt", "uarms"])
        agg = s.series("1", ["pt"], t0, t0 + 1800, max_points=50, res=900)
        self.assertEqual(agg["source"], "agregado")
        self.assertEqual(agg["t"], [t0, t0 + 900])
        self.assertAlmostEqual(agg["series"]["pt"]["avg"][0], 1100.0)
        self.assertEqual(agg["series"]["pt"]["max"][0], 1200.0)
        small = s.series("1", ["pt"], t0, t0 + 1800, max_points=50)
        self.assertEqual(small["res"], 60)
        self.assertEqual(len(small["t"]), 30)
        self.assertEqual(s.stat_extreme("1", "pt", t0, t0 + 1800, "max")["value"], 1200.0)

    def test_agrupamento_por_dia_no_fuso_local(self):
        s = self.store
        # 23:59:50 do dia 6 e 00:00:10 do dia 7, no horário de Brasília (03:00 UTC)
        a = local_epoch(self.clock, 2026, 10, 6, 23, 59, 50)
        s.ingest(reading(ec=(0, 0, 0, 10.0)), ts=a - 30)
        s.ingest(reading(ec=(0, 0, 0, 10.3)), ts=a)
        s.ingest(reading(ec=(0, 0, 0, 10.5)), ts=a + 20)
        res = s.energy("1", local_epoch(self.clock, 2026, 10, 6), local_epoch(self.clock, 2026, 10, 8), "day")
        self.assertEqual([r["label"] for r in res["rows"]], ["2026-10-06", "2026-10-07"])
        self.assertAlmostEqual(res["rows"][0]["c_t"], 0.3 + 0.1, places=6)
        self.assertAlmostEqual(res["rows"][1]["c_t"], 0.1, places=6)
        self.assertAlmostEqual(s.snapshot("1")["today"]["c_t"], 0.1, places=6)   # "hoje" zera à meia-noite local
        months = s.energy("1", a - 86400 * 40, a + 86400, "month")
        self.assertEqual(months["rows"][-1]["label"], "2026-10")

    def test_eventos_de_tensao(self):
        t0 = local_epoch(self.clock, 2026, 10, 7, 18, 0, 0)
        s = self.store
        volts = [127, 127, 112, 111, 109, 108, 113, 127, 127, 127]   # afunda na fase B e volta
        for i, v in enumerate(volts):
            s.ingest(reading(u=(127.0, float(v), 127.5)), ts=t0 + i * 30)
        evs = [e for e in s.events("1") if e["kind"] == "tensao"]
        self.assertEqual(len(evs), 1)
        ev = evs[0]
        self.assertEqual(ev["phase"], "b")
        self.assertEqual(ev["level"], "critico")           # chegou à faixa crítica (< 110 V)
        self.assertEqual(ev["ts"], t0 + 2 * 30)
        self.assertEqual(ev["end_ts"], t0 + 7 * 30)
        self.assertEqual(ev["data"]["min"], 108.0)
        self.assertFalse(ev["open"])

    def test_leitura_isolada_fora_da_faixa_nao_gera_evento(self):
        t0 = local_epoch(self.clock, 2026, 10, 7, 18, 0, 0)
        for i, v in enumerate([127, 127, 105, 127, 127]):
            self.store.ingest(reading(u=(float(v), 127.0, 127.0)), ts=t0 + i * 30)
        self.assertEqual([e for e in self.store.events("1") if e["kind"] == "tensao"], [])

    def test_falta_de_fase_e_fase_nunca_ligada(self):
        t0 = local_epoch(self.clock, 2026, 10, 7, 18, 0, 0)
        s = self.store
        # fase C nunca teve tensão (medidor usado em duas fases): não gera evento
        for i in range(3):
            s.ingest(reading(u=(127.0, 127.0, 0.3)), ts=t0 + i * 30)
        self.assertEqual(s.events("1"), [])
        # fase A cai a zero
        for i in range(3, 6):
            s.ingest(reading(u=(0.2, 127.0, 0.3)), ts=t0 + i * 30)
        evs = s.events("1")
        self.assertEqual([(e["kind"], e["phase"], e["open"]) for e in evs], [("falta_fase", "a", True)])

    def test_persistencia_apos_reinicio(self):
        t0 = local_epoch(self.clock, 2026, 10, 7, 10, 0, 0)
        s = self.store
        s.ingest(reading(ec=(1, 1, 1, 3.00)), ts=t0)
        s.ingest(reading(ec=(1.01, 1, 1, 3.01)), ts=t0 + 30)
        s.update_device("1", name="Casa", labels={"a": "Cozinha", "x": "ignorado"})
        s = self.reopen()
        info = s.snapshot("1")["device"]
        self.assertEqual(info["name"], "Casa")
        self.assertEqual(info["labels"]["a"], "Cozinha")
        self.assertEqual(info["labels"]["b"], "Fase B")
        self.assertEqual(info["n_readings"], 2)
        s.ingest(reading(ec=(1.03, 1, 1, 3.04)), ts=t0 + 60)       # continua da base salva
        e = s.energy_sum("1", t0, t0 + 900)
        self.assertAlmostEqual(e["c_t"], 0.04, places=6)
        self.assertAlmostEqual(s.snapshot("1")["totals"]["c_t"], 0.04, places=6)
        self.assertAlmostEqual(s.snapshot("1")["today"]["c_t"], 0.04, places=6)

    def test_ordem_e_duplicadas(self):
        t0 = local_epoch(self.clock, 2026, 10, 7, 10, 0, 0)
        s = self.store
        self.assertFalse(s.ingest(reading(), ts=t0)["skipped"])
        self.assertTrue(s.ingest(reading(), ts=t0)["skipped"])
        self.assertTrue(s.ingest(reading(), ts=t0 - 100)["skipped"])
        self.assertEqual(s.snapshot("1")["device"]["n_readings"], 1)

    def test_relogio_atrasado_ao_ligar_espera_o_acerto_da_hora(self):
        # Raspberry Pi sem bateria de relógio: liga com a hora antiga e acerta por NTP pouco depois.
        from unittest import mock
        s = self.store
        base = local_epoch(self.clock, 2026, 10, 7, 12, 0, 0)
        s.ingest(reading(ec=(0, 0, 0, 10.00)), ts=base - 30)
        s.ingest(reading(ec=(0, 0, 0, 10.01)), ts=base)
        s = self.reopen()                                        # servidor desligado por 3 h e religado
        with mock.patch("app.store.time.time") as fake:
            fake.return_value = base - 600                       # hora antiga logo após ligar
            r = s.ingest(reading(ec=(0, 0, 0, 12.50)))
            self.assertEqual((r["skipped"], r["reason"]), (True, "relogio"))
            fake.return_value = base + 3 * 3600                  # hora certa
            r = s.ingest(reading(ec=(0, 0, 0, 12.51)))
            self.assertFalse(r["skipped"])
        # a energia das 3 h fora do ar foi contada e repartida pelo intervalo
        self.assertAlmostEqual(s.snapshot("1")["totals"]["c_t"], 2.51, places=6)
        self.assertAlmostEqual(s.energy_sum("1", base, base + 3 * 3600 + 900)["c_t"], 2.50, places=6)
        self.assertEqual(s.snapshot("1")["device"]["n_readings"], 3)

    def test_relogio_que_voltou_no_tempo_nao_trava_a_gravacao(self):
        from unittest import mock
        from app.store import CLOCK_PATIENCE
        s = self.store
        base = local_epoch(self.clock, 2026, 10, 7, 12, 0, 0)
        with mock.patch("app.store.time.time") as fake, mock.patch("app.store.time.monotonic") as mono:
            fake.return_value, mono.return_value = base + 86400, 1000.0      # relógio um dia adiantado
            s.ingest(reading(ec=(0, 0, 0, 10.00)))
            fake.return_value, mono.return_value = base + 86430, 1030.0
            self.assertFalse(s.ingest(reading(ec=(0, 0, 0, 10.01)))["skipped"])
            # o relógio é corrigido e volta um dia: espera um pouco, depois segue gravando
            for i in range(CLOCK_PATIENCE):
                fake.return_value, mono.return_value = base + 30 * i, 1060.0 + 30 * i
                self.assertTrue(s.ingest(reading(ec=(0, 0, 0, 10.02)))["skipped"])
            fake.return_value, mono.return_value = base + 300, 1360.0
            r = s.ingest(reading(ec=(0, 0, 0, 10.30)))
            self.assertFalse(r["skipped"])
            self.assertEqual(r["ts"], base + 300)
            fake.return_value, mono.return_value = base + 330, 1390.0
            s.ingest(reading(ec=(0, 0, 0, 10.32)))
        self.assertEqual(s.snapshot("1")["device"]["n_readings"], 4)
        self.assertAlmostEqual(s.snapshot("1")["totals"]["c_t"], 0.32, places=6)     # nenhuma energia perdida
        self.assertAlmostEqual(s.energy_sum("1", base, base + 900)["c_t"], 0.31, places=6)
        self.assertEqual([e for e in s.events("1") if e["kind"] == "offline"], [])

    def test_duas_leituras_no_mesmo_segundo(self):
        from unittest import mock
        s = self.store
        with mock.patch("app.store.time.time") as fake:
            fake.return_value = 1_800_000_000.2
            a = s.ingest(reading())
            fake.return_value = 1_800_000_000.9
            b = s.ingest(reading())
        self.assertEqual((a["ts"], b["ts"]), (1_800_000_000, 1_800_000_001))

    def test_ingest_many(self):
        t0 = local_epoch(self.clock, 2026, 9, 1, 0, 0, 0)
        s = self.store
        items = [(t0 + i * 60, reading(ec=(0, 0, 0, round(i * 0.01, 2)), p=(600, 0, 0))) for i in range(3000)]
        n = s.ingest_many(items, source="teste", chunk=700)
        self.assertEqual(n, 3000)
        e = s.energy_sum("1", t0, t0 + 3000 * 60)
        self.assertAlmostEqual(e["c_t"], 29.99, places=4)
        stats = s.stat_rows("1", ["pt"], t0, t0 + 3000 * 60)
        self.assertEqual(len(stats), 200)
        self.assertTrue(all(r["n"] == 15 for r in stats))
        self.assertEqual(s.ingest_many(items[:10]), 0)           # repetidas são ignoradas
        days = s.energy("1", t0, t0 + 3 * 86400, "day")["rows"]
        self.assertAlmostEqual(days[0]["c_t"], 14.40, places=2)

    def test_ouvintes_e_offline(self):
        t0 = 1_800_000_000
        got = []
        s = self.store
        s.add_listener(got.append)
        s.ingest(reading(), ts=t0)
        self.assertEqual(got[0]["type"], "reading")
        self.assertTrue(got[0]["new_device"])
        self.assertEqual(got[0]["snapshot"]["reading"]["pt"], 600.0)
        s.state("1").online = True
        self.assertEqual(s.check_online(now=t0 + 60), [])
        self.assertEqual(s.check_online(now=t0 + 600), ["1"])
        self.assertEqual(got[-1], {"type": "offline", "device": "1"})
        self.assertEqual(s.check_online(now=t0 + 700), [])

    def test_configuracoes_e_exclusao(self):
        s = self.store
        self.assertEqual(s.settings()["mode"], "auto")
        s.update_settings({"tariff": "1.05", "mode": "bidirecional", "desconhecido": 1})
        self.assertEqual(s.settings()["tariff"], 1.05)
        with self.assertRaises(ValueError):
            s.update_settings({"mode": "xyz"})
        with self.assertRaises(ValueError):
            s.update_settings({"tariff": -1})
        s.ingest(reading(), ts=1_800_000_000)
        s.update_settings({"v_nominal": 220})
        self.assertEqual(s.snapshot("1")["device"]["v_nom"], 220.0)
        s.update_settings({"v_nominal": "auto"})
        self.assertEqual(s.snapshot("1")["device"]["v_nom"], 127.0)
        s = self.reopen()
        self.assertEqual(s.settings()["tariff"], 1.05)
        self.assertTrue(s.delete_device("1"))
        self.assertFalse(s.delete_device("1"))
        self.assertEqual(s.devices(), [])
        self.assertEqual(s.energy_sum("1", 0, 2_000_000_000)["n"], 0)

    def test_retencao_apaga_bruto_e_mantem_agregados(self):
        t0 = 1_800_000_000 - 1_800_000_000 % 900
        s = self.store
        for i in range(10):
            s.ingest(reading(ec=(0, 0, 0, i * 0.01)), ts=t0 + i * 30)
        removed = s.purge(retention_days=1, now=t0 + 3 * 86400)
        self.assertEqual(removed, 10)
        self.assertEqual(s.data_range("1")["raw_count"], 0)
        self.assertAlmostEqual(s.energy_sum("1", t0, t0 + 900)["c_t"], 0.09, places=6)
        ser = s.series("1", ["pt"], t0, t0 + 900)          # cai para os agregados
        self.assertEqual(ser["source"], "agregado")
        self.assertEqual(len(ser["t"]), 1)


if __name__ == "__main__":
    unittest.main()
