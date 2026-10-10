import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.solar import DemoSolar, SolarSource, compute_flow, parse_dtu_status, realtime, status_url
from app.timeutil import Clock

CLOCK = Clock("America/Sao_Paulo")


def dtu_doc(p_kw, stamp="2026-10-10 12:00:00", online=18, stale=False, age=1.0, e_today=12.5):
    return {
        "meta": {"stale": stale, "age_s": age, "last_error": "timed out" if stale else None},
        "summary": {"p_total_kw": p_kw, "e_total_kwh": 45000.0, "e_today_kwh": e_today, "panel_count": 20,
                    "panels_online": online, "panels_offline": 20 - online},
        "panels": [{"id": "1-1", "p_grid": p_kw * 1000 / 2, "online": online > 0, "last_update": stamp.replace(" ", "T")},
                   {"id": "1-2", "p_grid": p_kw * 1000 / 2, "online": online > 0, "last_update": None}],
    }


def local_ts(text):
    from datetime import datetime
    return datetime.fromisoformat(text).replace(tzinfo=CLOCK.tz).timestamp()


def solar_state(state="ok", p=0.0, age=10.0, expire=1200):
    return {"state": state, "p_w": p, "age_s": age, "expire_s": expire, "found": True, "auto": False}


class TestStatusUrl(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(status_url("http://h:8099"), "http://h:8099/api/status")
        self.assertEqual(status_url("h:8099/"), "http://h:8099/api/status")
        self.assertEqual(status_url("http://h:8099/api/summary"), "http://h:8099/api/status")
        self.assertEqual(status_url(""), "")


class TestParse(unittest.TestCase):
    def test_basic(self):
        s = parse_dtu_status(dtu_doc(2.34), CLOCK)
        self.assertAlmostEqual(s["p_w"], 2340.0)
        self.assertEqual(s["panels_online"], 18)
        self.assertAlmostEqual(s["dtu_time"], local_ts("2026-10-10T12:00:00"))

    def test_not_dtu(self):
        with self.assertRaises(ValueError):
            parse_dtu_status({"hello": 1}, CLOCK)


class TestSource(unittest.TestCase):
    def make(self, docs):
        it = iter(docs)
        return SolarSource(["http://dtu:8099"], CLOCK, poll_s=10, fetch=lambda url, t: next(it))

    def test_age_from_dtu_clock_then_changes(self):
        t0 = local_ts("2026-10-10T12:03:00")
        src = self.make([dtu_doc(2.0, "2026-10-10 12:00:00"), dtu_doc(2.0, "2026-10-10 12:00:00"),
                         dtu_doc(2.5, "2026-10-10 12:05:00"), dtu_doc(2.6, "2026-10-10 12:10:00"),
                         dtu_doc(2.7, "2026-10-10 12:15:00")])
        src.poll_once(t0)
        st = src.state(t0)
        self.assertEqual(st["state"], "atrasado")          # dado de 3 min atrás
        self.assertAlmostEqual(st["age_s"], 180 + 1, delta=2)
        src.poll_once(t0 + 10)
        src.poll_once(t0 + 125)                              # dado novo: idade conta a partir de agora
        st = src.state(t0 + 130)
        self.assertEqual(st["state"], "ok")
        self.assertLess(st["age_s"], 10)
        src.poll_once(t0 + 425)
        src.poll_once(t0 + 725)
        self.assertEqual(src.interval(), 300)

    def test_wrong_dtu_clock_is_ignored(self):
        t0 = local_ts("2026-10-10T12:03:00")
        src = self.make([dtu_doc(2.0, "2026-10-11 20:00:00")])   # relógio do DTU adiantado
        src.poll_once(t0)
        st = src.state(t0 + 5)
        self.assertFalse(st["age_known"])
        self.assertEqual(st["state"], "ok")

    def test_expired_and_night(self):
        t0 = local_ts("2026-10-10T18:40:00")
        src = self.make([dtu_doc(0.05, "2026-10-10 18:00:00")])
        src.poll_once(t0)
        self.assertEqual(src.state(t0)["state"], "expirado")
        src = self.make([dtu_doc(0.05, "2026-10-10 18:00:00", online=0)])
        src.poll_once(t0)
        self.assertEqual(src.state(t0)["state"], "noite")

    def test_source_down(self):
        t0 = 1_800_000_000

        def boom(url, timeout):
            raise OSError("connection refused")
        src = SolarSource(["http://x"], CLOCK, fetch=boom)
        self.assertFalse(src.poll_once(t0))
        self.assertEqual(src.state(t0)["state"], "fora")
        self.assertIn("connection refused", src.state(t0)["error"])
        src = SolarSource(["http://x"], CLOCK, fetch=boom, auto=True)
        src.poll_once(t0)
        self.assertEqual(src.state(t0)["state"], "procurando")

    def test_falls_back_to_second_url(self):
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            if "first" in url:
                raise OSError("no route")
            return dtu_doc(1.0)
        src = SolarSource(["http://first:8099", "http://second:8099"], CLOCK, fetch=fetch)
        self.assertTrue(src.poll_once(1_800_000_000))
        self.assertEqual(src.url, "http://second:8099/api/status")
        src.poll_once(1_800_000_010)
        self.assertEqual(calls[-1], "http://second:8099/api/status")   # fica no endereço que respondeu

    def test_real_http(self):
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(dtu_doc(1.5)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            src = SolarSource(["127.0.0.1:%d" % srv.server_address[1]], CLOCK)
            self.assertTrue(src.poll_once())
            self.assertAlmostEqual(src.state()["p_w"], 1500.0)
        finally:
            srv.shutdown()
            srv.server_close()


class TestAlignment(unittest.TestCase):
    def source(self):
        # DTU rápido: dado novo a cada 10 s, com a potência subindo 100 W por leitura
        t0 = local_ts("2026-10-10T12:00:00")
        docs = [dtu_doc((1000 + 100 * i) / 1000.0, "2026-10-10 12:00:%02d" % (10 * i), age=0.0) for i in range(6)]
        src = SolarSource(["http://dtu"], CLOCK, fetch=lambda u, t, it=iter(docs): next(it))
        for i in range(6):
            src.poll_once(t0 + 10 * i)
        return src, t0

    def test_interpolates_between_dtu_points(self):
        src, t0 = self.source()
        p, gap = src.value_at(t0 + 25)
        self.assertAlmostEqual(p, 1250.0)
        self.assertEqual(gap, 0.0)
        p, gap = src.value_at(t0 + 80)                 # depois do último dado: vale o último
        self.assertAlmostEqual(p, 1500.0)
        self.assertAlmostEqual(gap, 30.0)

    def test_flow_aligned_to_meter_reading(self):
        src, t0 = self.source()
        now = t0 + 52
        st = src.at(t0 + 20, now)                     # leitura do medidor de 32 s atrás
        self.assertEqual(st["p_w"], 1200.0)
        self.assertEqual(st["state"], "ok")
        f = compute_flow(-200, True, st, "rede")
        self.assertEqual(f["home"]["w"], 1000)         # a casa sai certa, com o solar do mesmo instante
        rt = realtime(f, src.state(now), 32)
        self.assertEqual(rt["solar"]["w"], 1500)
        self.assertEqual(rt["home"]["w"], 1000)
        self.assertEqual(rt["grid"]["w"], -500)        # mais sol desde a leitura: mais injeção estimada
        self.assertTrue(rt["grid"]["est"])
        self.assertEqual(rt["flows"], {"grid_home": 0, "solar_grid": 500, "solar_home": 1000})

    def test_realtime_keeps_lower_bounds(self):
        f = compute_flow(-700, True, solar_state("expirado", p=3000, age=3600), "rede")
        self.assertIs(realtime(f, solar_state("expirado", p=3000), 10), f)


class TestFlow(unittest.TestCase):
    def test_importing_with_solar(self):
        f = compute_flow(800, True, solar_state(p=1200), "rede")
        self.assertEqual(f["home"]["w"], 2000)
        self.assertEqual(f["flows"], {"solar_home": 1200, "solar_grid": 0, "grid_home": 800})
        self.assertAlmostEqual(f["home"]["solar_share"], 0.6)
        self.assertEqual(f["notes"], [])

    def test_exporting(self):
        f = compute_flow(-1500, True, solar_state(p=2500), "rede")
        self.assertEqual(f["home"]["w"], 1000)
        self.assertEqual(f["flows"], {"solar_home": 1000, "solar_grid": 1500, "grid_home": 0})

    def test_solar_behind_export_is_corrected(self):
        f = compute_flow(-1500, True, solar_state("atrasado", p=1000, age=200), "rede")
        self.assertEqual(f["solar"]["w"], 1500)
        self.assertTrue(f["solar"]["est"])
        self.assertEqual(f["home"]["w"], 0)
        self.assertIn("solar_ajustado", [n["code"] for n in f["notes"]])

    def test_small_mismatch_is_silent(self):
        f = compute_flow(-1020, True, solar_state(p=1000), "rede")
        self.assertEqual(f["home"]["w"], 0)
        self.assertEqual(f["notes"], [])

    def test_delayed_solar_marks_home_estimated(self):
        f = compute_flow(500, True, solar_state("atrasado", p=1000, age=240), "rede")
        self.assertEqual(f["home"]["w"], 1500)
        self.assertTrue(f["home"]["est"])
        self.assertIn("solar_atrasado", [n["code"] for n in f["notes"]])

    def test_expired_uses_meter_minimum(self):
        f = compute_flow(-700, True, solar_state("expirado", p=3000, age=3600), "rede")
        self.assertEqual(f["solar"]["w"], 700)
        self.assertTrue(f["solar"]["min"])
        self.assertIsNone(f["home"]["w"])
        f = compute_flow(900, True, solar_state("fora"), "rede")
        self.assertEqual(f["home"]["w"], 900)
        self.assertTrue(f["home"]["min"])

    def test_night(self):
        f = compute_flow(450, True, solar_state("noite", p=35, age=9000), "rede")
        self.assertEqual(f["solar"]["w"], 0)
        self.assertFalse(f["solar"]["est"])
        self.assertEqual(f["flows"], {"solar_home": 0, "solar_grid": 0, "grid_home": 450})

    def test_meter_on_loads(self):
        f = compute_flow(1800, True, solar_state(p=2500), "cargas")
        self.assertEqual(f["home"]["w"], 1800)
        self.assertEqual(f["grid"]["w"], -700)
        self.assertEqual(f["flows"], {"solar_home": 1800, "solar_grid": 700, "grid_home": 0})
        # injeção vista pelo medidor: ele está na entrada da rede, não nas cargas
        f = compute_flow(-600, True, solar_state(p=2500), "cargas")
        self.assertEqual(f["ref"], "rede")
        self.assertEqual(f["home"]["w"], 1900)

    def test_meter_offline(self):
        f = compute_flow(500, False, solar_state(p=1000), "rede")
        self.assertIsNone(f["grid"]["w"])
        self.assertEqual(f["solar"]["w"], 1000)
        self.assertIn("medidor_fora", [n["code"] for n in f["notes"]])

    def test_generation_mode_has_no_flow(self):
        self.assertFalse(compute_flow(500, True, solar_state(p=1000), "rede", mode="geracao")["available"])


class TestDemo(unittest.TestCase):
    def test_lags_behind(self):
        src = DemoSolar(CLOCK, lambda t: 1000.0 + t % 1000, step_s=300, lag_s=20)
        t = local_ts("2026-10-10T12:07:00")
        src.poll_once(t)
        st = src.state(t)
        self.assertEqual(st["interval_s"], 300)
        self.assertAlmostEqual(st["age_s"], 120)
        self.assertEqual(st["state"], "atrasado")


class TestLiveApi(unittest.TestCase):
    def test_live_includes_flow(self):
        from app.config import Config
        from app.server import App
        with tempfile.TemporaryDirectory() as d:
            app = App(Config(data_dir=d, demo=False))
            app.solar = SolarSource(["http://dtu"], app.clock, fetch=lambda u, t: dtu_doc(1.0, age=0.0))
            app.core.solar = app.solar
            body = json.dumps({"id": "77", "pa": "300", "pb": "200", "pc": "100", "ua": "127", "ub": "127",
                               "uc": "127", "ept_c": "1", "ept_g": "0"}).encode()
            self.assertTrue(app.ingest_raw(body, "", "http")["ok"])
            app.solar.poll_once()
            live = app.core.api_get("/api/live", {"device": "77"})
            self.assertIn("flow", live)
            self.assertEqual(live["flow"]["solar"]["w"], 1000)
            self.assertEqual(live["flow"]["home"]["w"], 1600)
            st = app.core.status()
            self.assertEqual(st["solar"]["url"], "http://dtu/api/status")
            app.store.update_settings({"solar_sync": "tempo_real"})
            live = app.core.api_get("/api/live", {"device": "77"})
            self.assertEqual(live["flow"]["sync"], "tempo_real")
            self.assertTrue(live["flow"]["grid"]["est"])
            with self.assertRaises(ValueError):
                app.store.update_settings({"solar_sync": "x"})
            app.store.update_settings({"solar_ref": "cargas"})
            with self.assertRaises(ValueError):
                app.store.update_settings({"solar_ref": "x"})
            app.stop()


if __name__ == "__main__":
    unittest.main()
