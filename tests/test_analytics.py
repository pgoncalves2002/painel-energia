import os
import tempfile
import unittest
from datetime import datetime

from app import analytics, voltage
from app.parser import normalize
from app.simulate import HouseSim, as_meter_payload
from app.store import Store
from app.timeutil import Clock
from tests.test_store import local_epoch, reading

TZ = "America/Sao_Paulo"


class VoltageTest(unittest.TestCase):
    def test_limites_tabelados(self):
        self.assertEqual(voltage.limits(127), {"crit_low": 110.0, "adeq_low": 117.0, "adeq_high": 133.0, "crit_high": 135.0})
        self.assertEqual(voltage.limits(220), {"crit_low": 191.0, "adeq_low": 202.0, "adeq_high": 231.0, "crit_high": 233.0})
        lim = voltage.limits(240)       # fora da tabela: usa os fatores 0,87 / 0,92 / 1,05 / 1,06
        self.assertEqual((lim["crit_low"], lim["adeq_low"], lim["adeq_high"], lim["crit_high"]), (208.8, 220.8, 252.0, 254.4))

    def test_classificacao(self):
        cases = {127.0: "adequada", 117.0: "adequada", 133.0: "adequada", 116.9: "precaria", 110.0: "precaria",
                 133.1: "precaria", 135.0: "precaria", 109.9: "critica", 135.1: "critica", 20.0: "ausente", 0.0: "ausente"}
        for u, expected in cases.items():
            self.assertEqual(voltage.classify(u, 127.0), expected, u)
        self.assertIsNone(voltage.classify(None, 127.0))
        self.assertIsNone(voltage.classify(127.0, None))

    def test_tensao_nominal(self):
        self.assertEqual(voltage.detect_nominal(125.8), 127.0)
        self.assertEqual(voltage.detect_nominal(218.0), 220.0)
        self.assertIsNone(voltage.detect_nominal(0.3))
        self.assertIsNone(voltage.detect_nominal(None))


class ClockTest(unittest.TestCase):
    def test_limites_de_dia_e_mes(self):
        c = Clock(TZ)
        t = local_epoch(c, 2026, 10, 7, 15, 30)
        self.assertEqual(c.day_start(t), local_epoch(c, 2026, 10, 7))
        self.assertEqual(c.month_start(t), local_epoch(c, 2026, 10, 1))
        self.assertEqual(c.add_days(t, -7), local_epoch(c, 2026, 9, 30))
        self.assertEqual(c.add_months(t, 3), local_epoch(c, 2027, 1, 1))
        self.assertEqual(c.add_months(t, -10), local_epoch(c, 2025, 12, 1))
        self.assertEqual(c.days_in_month(local_epoch(c, 2028, 2, 10)), 29)
        self.assertEqual(c.offset(t), -3 * 3600)

    def test_agrupamentos(self):
        c = Clock(TZ)
        a, b = local_epoch(c, 2026, 12, 30, 10), local_epoch(c, 2027, 1, 2, 9)
        days = c.boundaries(a, b, "day")
        self.assertEqual([c.label(x, "day") for x in days], ["2026-12-30", "2026-12-31", "2027-01-01", "2027-01-02", "2027-01-03"])
        months = c.boundaries(a, b, "month")
        self.assertEqual([c.label(x, "month") for x in months], ["2026-12", "2027-01", "2027-02"])
        hours = c.boundaries(a + 600, a + 7300, "hour")
        self.assertEqual([c.label(x, "hour") for x in hours], ["2026-12-30T10:00", "2026-12-30T11:00", "2026-12-30T12:00", "2026-12-30T13:00"])
        q = c.boundaries(a + 100, a + 1000, "15min")
        self.assertEqual(q, [a, a + 900, a + 1800])
        with self.assertRaises(ValueError):
            c.boundaries(a, b, "semana")

    def test_horario_de_verao_em_outros_fusos(self):
        # Nova York, 8/3/2026: o dia tem 23 horas (os relógios adiantam às 2h)
        c = Clock("America/New_York")
        d0 = int(datetime(2026, 3, 8, tzinfo=c.tz).timestamp())
        days = c.boundaries(d0, d0 + 1, "day")
        self.assertEqual(days[1] - days[0], 23 * 3600)
        hours = c.boundaries(days[0], days[1], "hour")
        self.assertEqual(len(hours) - 1, 23)
        self.assertEqual(c.label(hours[2], "hour"), "2026-03-08T03:00")
        # fuso com meia hora (Índia): os grupos de hora começam no minuto 0 local
        india = Clock("Asia/Kolkata")
        t = int(datetime(2026, 5, 1, 10, 20, tzinfo=india.tz).timestamp())
        self.assertEqual(india.label(india.boundaries(t, t + 1, "hour")[0], "hour"), "2026-05-01T10:00")

    def test_fuso_invalido_cai_para_utc(self):
        c = Clock("Lugar/Inexistente")
        self.assertEqual(c.name, "UTC")
        self.assertEqual(c.offset(1_800_000_000), 0)


class AnalyticsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock(TZ)
        self.store = Store(os.path.join(self.tmp.name, "t.db"), self.clock, tariff_default=1.0)

    def tearDown(self):
        self.store.close_thread()
        self.tmp.cleanup()

    def fill(self, start, end, kw_by_hour, step=300, gen_kw_by_hour=None):
        """Leituras a cada `step` s com potência constante por hora do dia (kW) e contadores coerentes."""
        items = []
        ec = eg = 0.0
        prev = None
        for ts in range(start, end, step):
            hour = self.clock.local(ts).hour
            kw = kw_by_hour(hour)
            gkw = gen_kw_by_hour(hour) if gen_kw_by_hour else 0.0
            if prev is not None:
                ec += kw * step / 3600.0
                eg += gkw * step / 3600.0
            prev = ts
            items.append((ts, reading(p=((kw - gkw) * 1000.0, 0.0, 0.0), ec=(ec, 0, 0, ec), eg=(eg, 0, 0, eg))))
        self.store.ingest_many(items, source="teste")

    def test_resumo(self):
        c = self.clock
        now = local_epoch(c, 2026, 10, 7, 12, 0, 0)
        # 1 kW constante durante 40 dias, 0,2 kW de madrugada (1h–5h)
        self.fill(local_epoch(c, 2026, 8, 28), now + 1, lambda h: 0.2 if 1 <= h < 5 else 1.0)
        s = analytics.summary(self.store, "1", now=now)
        day_kwh = 20 * 1.0 + 4 * 0.2
        self.assertEqual(s["mode"], "consumo")
        self.assertAlmostEqual(s["today"]["c_t"], 8 * 1.0 + 4 * 0.2, places=2)        # 0h–12h
        self.assertAlmostEqual(s["yesterday"]["c_t"], day_kwh, places=2)
        self.assertAlmostEqual(s["yesterday_same_time"]["c_t"], 8.8, places=2)
        self.assertAlmostEqual(s["today"]["cost"], 8.8, places=2)
        self.assertAlmostEqual(s["month"]["c_t"], 6 * day_kwh + 8.8, places=1)
        self.assertAlmostEqual(s["last_month"]["c_t"], 30 * day_kwh, places=1)
        self.assertAlmostEqual(s["last_month_same_time"]["c_t"], 6 * day_kwh + 8.8, places=1)
        self.assertAlmostEqual(s["week"]["c_t"], 6 * day_kwh + 8.8, places=1)
        self.assertAlmostEqual(s["prev_week"]["c_t"], 7 * day_kwh, places=1)
        # projeção: realizado + média dos dias completos x dias restantes (24,5 dias)
        self.assertEqual(s["projection"]["based_on_days"], 7)
        self.assertAlmostEqual(s["projection"]["c_t"], 6 * day_kwh + 8.8 + day_kwh * 24.5, places=0)
        self.assertAlmostEqual(s["demand_today"]["kw"], 1.0, places=2)
        self.assertAlmostEqual(s["peak_today"]["value"], 1000.0, places=0)
        self.assertAlmostEqual(s["base_load"]["w"], 200.0, places=0)
        self.assertAlmostEqual(s["base_load"]["kwh_month"], 144.0, places=0)
        self.assertEqual(s["first_data"], local_epoch(c, 2026, 8, 28))

    def test_modo_deduzido_dos_dados(self):
        c = self.clock
        now = local_epoch(c, 2026, 10, 7, 12)
        self.fill(now - 3 * 86400, now + 1, lambda h: 0.5, gen_kw_by_hour=lambda h: 3.0 if 9 <= h < 15 else 0.0)
        s = analytics.summary(self.store, "1", now=now)
        self.assertEqual(s["mode"], "bidirecional")
        self.assertLess(s["yesterday"]["net"], 0)
        self.assertEqual(analytics.effective_mode({"mode": "auto"}, {"c_t": 100.0, "g_t": 0.0}), "consumo")
        self.assertEqual(analytics.effective_mode({"mode": "auto"}, {"c_t": 100.0, "g_t": 1.0}), "consumo")
        self.assertEqual(analytics.effective_mode({"mode": "auto"}, {"c_t": 100.0, "g_t": 30.0}), "bidirecional")
        self.assertEqual(analytics.effective_mode({"mode": "auto"}, {"c_t": 0.2, "g_t": 300.0}), "geracao")
        self.assertEqual(analytics.effective_mode({"mode": "consumo"}, {"c_t": 1.0, "g_t": 300.0}), "consumo")
        # medidor recém-ligado com solar: poucos minutos de injeção já mudam o modo
        self.assertEqual(analytics.effective_mode({"mode": "auto"}, {"c_t": 0.0, "g_t": 0.09}), "bidirecional")
        self.assertEqual(analytics.effective_mode({"mode": "auto"}, {"c_t": 0.0, "g_t": 4.0}), "bidirecional")
        self.assertEqual(analytics.effective_mode({"mode": "auto"}, {"c_t": 0.1, "g_t": 12.0}), "geracao")
        self.assertEqual(analytics.effective_mode({"mode": "auto"}, {"c_t": 0.4, "g_t": 0.09}), "bidirecional")
        self.assertEqual(analytics.effective_mode({"mode": "auto"}, {"c_t": 0.4, "g_t": 0.03}), "consumo")
        self.store.update_settings({"credit": 0.5})
        s = analytics.summary(self.store, "1", now=now)
        y = s["yesterday"]
        self.assertAlmostEqual(y["cost"], round(y["c_t"] * 1.0 - y["g_t"] * 0.5, 2), places=2)

    def test_energia_ate_um_instante(self):
        c = self.clock
        t0 = local_epoch(c, 2026, 10, 7, 0)
        self.fill(t0, t0 + 4 * 3600 + 1, lambda h: 1.2)
        e = analytics.energy_until(self.store, "1", t0, t0 + 3600 + 450)      # 1 h e meio intervalo de 15 min
        self.assertAlmostEqual(e["c_t"], 1.2 * (1 + 0.125), places=3)
        self.assertEqual(analytics.energy_until(self.store, "1", t0, t0 - 50)["c_t"], 0.0)

    def test_qualidade(self):
        c = self.clock
        t0 = local_epoch(c, 2026, 10, 1, 0)
        items = []
        # 100 janelas de 10 min: fase A sempre boa; fase B com 10 janelas precárias e 2 críticas; fase C sem tensão
        for w in range(100):
            ub = 112.0 if w < 10 else 104.0 if w < 12 else 126.0
            for k in range(5):
                ts = t0 + w * 600 + k * 120
                items.append((ts, reading(p=(900.0, 100.0, 0.0), u=(127.0 + (k % 2), ub, 0.4), freq=60.0 if w else 59.7,
                                          sa=1000.0, sb=100.0, sc=0.0, st=1100.0)))
        self.store.ingest_many(items, source="teste")
        q = analytics.quality(self.store, "1", t0, t0 + 100 * 600)
        self.assertEqual((q["v_nom"], q["window"], q["source"], q["n_windows"]), (127.0, 600, "bruto", 100))
        a, b, cph = q["phases"]["a"], q["phases"]["b"], q["phases"]["c"]
        self.assertEqual(a["counts"], {"adequada": 100, "precaria": 0, "critica": 0, "ausente": 0})
        self.assertTrue(a["ok"])
        self.assertEqual((a["min"]["value"], a["max"]["value"]), (127.0, 128.0))
        self.assertEqual(b["counts"], {"adequada": 88, "precaria": 10, "critica": 2, "ausente": 0})
        self.assertEqual((b["drp"], b["drc"]), (10.0, 2.0))
        self.assertFalse(b["ok"])
        self.assertEqual(b["min"]["value"], 104.0)
        self.assertEqual(cph["counts"]["ausente"], 100)
        self.assertIsNone(cph["drp"])
        self.assertIsNone(q["imbalance"])                 # com uma fase sem tensão não há o que comparar
        self.assertEqual(q["freq"]["within"], 99.0)
        self.assertEqual(q["freq"]["min"]["value"], 59.7)
        self.assertEqual(q["pf"]["t"], {"avg": 0.909, "below": 100.0, "n": 100})     # 1000 W / 1100 VA
        self.assertEqual(q["pf"]["a"]["avg"], 0.9)
        self.assertEqual(q["pf"]["b"]["below"], 0.0)
        self.assertNotIn("c", q["pf"])                    # fase sem carga não entra
        self.assertEqual(dict(b["hist"])[126], 88)

    def test_qualidade_usa_agregados_quando_o_bruto_ja_foi_apagado(self):
        c = self.clock
        t0 = local_epoch(c, 2026, 9, 1, 0)
        self.store.ingest_many([(t0 + i * 60, reading(u=(127.0, 126.0, 128.0))) for i in range(180)], source="teste")
        self.store.purge(1, now=t0 + 10 * 86400)
        q = analytics.quality(self.store, "1", t0, t0 + 3 * 3600)
        self.assertEqual((q["source"], q["window"], q["n_windows"]), ("agregado", 900, 12))
        self.assertEqual(q["phases"]["a"]["counts"]["adequada"], 12)
        self.assertAlmostEqual(q["imbalance"]["avg"], 0.79, places=2)


class SimulatorTest(unittest.TestCase):
    def test_deterministico_e_coerente(self):
        c = Clock(TZ)
        t0 = local_epoch(c, 2026, 10, 5, 0)
        a, b = HouseSim(c, seed=3), HouseSim(c, seed=3)
        self.assertEqual(a.loads(t0 + 7 * 3600), b.loads(t0 + 7 * 3600))
        prev = None
        total_kwh = 0.0
        for ts in range(t0, t0 + 86400 + 1, 60):
            r = a.reading(ts)
            self.assertTrue(100 < r["uarms"] < 140 and 100 < r["ubrms"] < 140)
            self.assertAlmostEqual(r["pt"], r["pa"] + r["pb"] + r["pc"], places=6)
            self.assertTrue(0.5 < r["pft"] <= 1.0)
            if prev is not None:
                for k in ("epa_c", "epb_c", "epc_c", "ept_c"):
                    self.assertGreaterEqual(r[k], prev[k])
                self.assertEqual(r["ept_g"], 0.0)
            prev = r
        total_kwh = prev["ept_c"]
        self.assertTrue(5 < total_kwh < 25, total_kwh)                         # um dia de casa: entre 5 e 25 kWh
        self.assertAlmostEqual(prev["ept_c"], prev["epa_c"] + prev["epb_c"] + prev["epc_c"], delta=0.05)

    def test_solar_gera_e_injeta(self):
        c = Clock(TZ)
        t0 = local_epoch(c, 2026, 10, 5, 0)
        sim = HouseSim(c, seed=3, solar_kwp=4.0)
        noon = None
        for ts in range(t0, t0 + 86400 + 1, 120):
            r = sim.reading(ts)
            if ts == t0 + 12 * 3600:
                noon = r
        self.assertLess(noon["pt"], 0)                      # ao meio-dia sobra energia
        self.assertGreater(r["ept_g"], 3.0)
        self.assertGreater(r["ept_c"], 1.0)
        self.assertEqual(sim.solar(t0 + 2 * 3600), 0.0)     # de madrugada não gera

    def test_formato_do_medidor_e_retomada(self):
        c = Clock(TZ)
        sim = HouseSim(c, device_id="abc")
        sim.reading(1_800_000_000)
        payload = as_meter_payload(sim.reading(1_800_000_030))
        self.assertEqual(payload["id"], "abc")
        self.assertRegex(payload["uarms"], r"^\d+\.\d\d$")
        self.assertEqual(len(payload), 41)                  # 40 campos do SM-3W Lite + rssi_wifi
        self.assertEqual(normalize({k.lower(): v for k, v in payload.items()})["_phases"], 3)
        other = HouseSim(c, device_id="abc")
        other.resume({"ept_c": 123.45, "epa_c": 50.0}, 1_800_000_030)
        r = other.reading(1_800_000_090)
        self.assertGreaterEqual(r["ept_c"], 123.45)
        self.assertGreaterEqual(r["epa_c"], 50.0)


class CounterCheckTest(unittest.TestCase):
    """Conferência dos contadores de energia do medidor contra a potência medida."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock(TZ)
        self.store = Store(os.path.join(self.tmp.name, "t.db"), self.clock)
        self.now = local_epoch(self.clock, 2026, 10, 7, 12, 0, 0)

    def tearDown(self):
        self.store.close_thread()
        self.tmp.cleanup()

    def fill(self, hours, kw, gen_kw=0.0, step=30, scale=1.0, counters=True):
        """Leituras a cada `step` s; os contadores avançam de 0,01 em 0,01 (x scale), como no medidor."""
        items = []
        ec = eg = 0.0
        start = self.now - int(hours * 3600)
        for i, ts in enumerate(range(start, self.now + 1, step)):
            if i:
                ec += kw * step / 3600.0
                eg += gen_kw * step / 3600.0
            c, g = round(int(ec * 100) / 100.0 * scale, 6), round(int(eg * 100) / 100.0 * scale, 6)
            items.append((ts, reading(p=((kw - gen_kw) * 1000.0, 0.0, 0.0),
                                      ec=(c, 0, 0, c) if counters else None, eg=(g, 0, 0, g) if counters else None)))
        self.store.ingest_many(items, source="teste")

    def test_contadores_em_kwh_batem_com_a_potencia(self):
        self.fill(6, 1.0)
        c = analytics.counter_check(self.store, "1", now=self.now)
        self.assertEqual(c["status"], "ok")
        self.assertAlmostEqual(c["power_kwh"], 6.0, places=2)
        self.assertAlmostEqual(c["counter_kwh"], 6.0, places=1)
        self.assertAlmostEqual(c["ratio"], 1.0, places=1)

    def test_com_geracao_solar(self):
        self.fill(6, 0.4, gen_kw=1.6)                 # injetando 1,2 kW
        c = analytics.counter_check(self.store, "1", now=self.now)
        self.assertEqual(c["status"], "ok")
        self.assertAlmostEqual(c["power_kwh"], 7.2, places=1)

    def test_contadores_em_wh_sao_detectados(self):
        self.fill(6, 1.0, scale=1000.0)
        c = analytics.counter_check(self.store, "1", now=self.now)
        self.assertEqual(c["status"], "wh")
        self.assertAlmostEqual(c["ratio"], 1000.0, delta=50)
        # sem a correção, o painel descarta esses avanços como saltos impossíveis: o consumo fica zerado
        self.assertEqual(self.store.snapshot("1")["totals"]["c_t"], 0.0)

    def test_unidade_wh_configurada_para_medidor_em_kwh(self):
        self.fill(6, 1.0, scale=0.001)
        self.assertEqual(analytics.counter_check(self.store, "1", now=self.now)["status"], "kwh")

    def test_contador_parado_com_potencia(self):
        self.fill(6, 1.0, scale=0.0)                  # contador sempre em zero
        c = analytics.counter_check(self.store, "1", now=self.now)
        self.assertEqual((c["status"], c["ratio"]), ("divergente", 0.0))

    def test_pouca_energia_ou_sem_contadores(self):
        self.fill(0.5, 0.1)                           # 0,05 kWh: pouco para comparar
        self.assertEqual(analytics.counter_check(self.store, "1", now=self.now)["status"], "aguardando")
        self.assertEqual(analytics.counter_check(self.store, "outro", now=self.now)["status"], "sem_contador")

    def test_medidor_sem_contadores(self):
        self.fill(2, 1.0, counters=False)
        self.assertEqual(analytics.counter_check(self.store, "1", now=self.now)["status"], "sem_contador")


if __name__ == "__main__":
    unittest.main()
