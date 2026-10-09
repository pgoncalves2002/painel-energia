import unittest

from app.energy import counter_delta, energy_cap, integrate_power, split_buckets


class CounterDeltaTest(unittest.TestCase):
    def test_primeira_leitura_nao_conta(self):
        self.assertEqual(counter_delta(None, 312.4, None), (0.0, 312.4, "primeira"))

    def test_contador_ausente(self):
        self.assertEqual(counter_delta(10.0, None, 30), (0.0, 10.0, "ausente"))

    def test_diferenca_normal(self):
        d, base, status = counter_delta(100.00, 100.03, 30)
        self.assertAlmostEqual(d, 0.03)
        self.assertEqual((base, status), (100.03, "ok"))

    def test_lacuna_longa_ainda_e_valida(self):
        # app fora do ar por 2 dias: 40 kWh em 172800 s é plausível
        d, base, status = counter_delta(100.0, 140.0, 172800)
        self.assertAlmostEqual(d, 40.0)
        self.assertEqual(status, "ok")

    def test_salto_absurdo_e_descartado(self):
        d, base, status = counter_delta(100.0, 3000.0, 30)
        self.assertEqual((d, base, status), (0.0, 3000.0, "salto"))

    def test_reinicio_mensal(self):
        d, base, status = counter_delta(312.40, 0.02, 30)
        self.assertAlmostEqual(d, 0.02)
        self.assertEqual((base, status), (0.02, "reinicio"))

    def test_zeros_ao_religar_nao_geram_pico(self):
        # o medidor religa, envia 0 e depois recupera o contador salvo
        d1, base, s1 = counter_delta(300.00, 0.00, 30)
        self.assertEqual((d1, base, s1), (0.0, 0.0, "reinicio"))
        d2, base, s2 = counter_delta(base, 300.02, 30)
        self.assertEqual((d2, base, s2), (0.0, 300.02, "salto"))
        d3, base, s3 = counter_delta(base, 300.05, 30)
        self.assertAlmostEqual(d3, 0.03)
        self.assertEqual(s3, "ok")

    def test_recuo_apos_queda_de_energia_nao_soma_o_contador(self):
        # voltou ao último valor salvo (1,2 kWh abaixo) depois de 6 h fora do ar
        d, base, status = counter_delta(300.00, 298.80, 6 * 3600)
        self.assertEqual((d, base, status), (0.0, 298.80, "recuo"))

    def test_pequeno_recuo(self):
        d, base, status = counter_delta(300.00, 299.99, 30)
        self.assertEqual((d, base, status), (0.0, 299.99, "recuo"))
        d, base, status = counter_delta(base, 300.02, 30)
        self.assertAlmostEqual(d, 0.03)

    def test_teto(self):
        self.assertAlmostEqual(energy_cap(3600, 80), 80.05)
        self.assertAlmostEqual(energy_cap(None, 80), 80 * 60 / 3600 + 0.05)


class SplitTest(unittest.TestCase):
    def test_mesmo_intervalo(self):
        self.assertEqual(split_buckets(900 + 10, 900 + 40), [(900, 1.0)])

    def test_sem_leitura_anterior(self):
        self.assertEqual(split_buckets(None, 1000), [(900, 1.0)])

    def test_cruza_um_limite(self):
        parts = split_buckets(1790, 1820)          # 10 s antes e 20 s depois de 1800
        self.assertEqual([b for b, _ in parts], [900, 1800])
        self.assertAlmostEqual(parts[0][1], 1 / 3)
        self.assertAlmostEqual(parts[1][1], 2 / 3)

    def test_lacuna_de_varias_horas(self):
        parts = split_buckets(0, 4 * 3600)
        self.assertEqual(len(parts), 16)
        self.assertAlmostEqual(sum(f for _, f in parts), 1.0)
        self.assertTrue(all(abs(f - 1 / 16) < 1e-9 for _, f in parts))

    def test_tempo_invertido(self):
        self.assertEqual(split_buckets(2000, 1900), [(1800, 1.0)])


class IntegrateTest(unittest.TestCase):
    def test_consumo(self):
        cons, gen = integrate_power(1000, 1000, 3600)
        self.assertAlmostEqual(cons, 1.0)
        self.assertEqual(gen, 0.0)

    def test_geracao(self):
        cons, gen = integrate_power(-2000, -1000, 1800)
        self.assertEqual(cons, 0.0)
        self.assertAlmostEqual(gen, 0.75)

    def test_cruzando_zero(self):
        cons, gen = integrate_power(1000, -1000, 3600)
        self.assertAlmostEqual(cons, 0.25)
        self.assertAlmostEqual(gen, 0.25)

    def test_sem_dados(self):
        self.assertEqual(integrate_power(None, None, 30), (0.0, 0.0))
        self.assertEqual(integrate_power(100, 100, 0), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
