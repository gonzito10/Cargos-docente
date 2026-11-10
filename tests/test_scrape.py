import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import scrape  # noqa: E402

CFG = json.loads((Path(scrape.__file__).with_name("sources.json")).read_text(encoding="utf-8"))


class Parsing(unittest.TestCase):
    def test_cierre_rango_sin_espacio_en_anio(self):
        t = "Inscripción: 3 de agosto al 08 de agosto2026 Recusaciones 10, 11 y 12 de agosto"
        self.assertEqual(scrape.parse_cierre(t, 2026), "2026-08-08")

    def test_cierre_hasta(self):
        t = "Las inscripciones se recibirán hasta el 15 de marzo de 2026 por correo."
        self.assertEqual(scrape.parse_cierre(t, 2025), "2026-03-15")

    def test_cierre_rango_sin_anio_usa_default(self):
        self.assertEqual(scrape.parse_cierre("Inscripción del 10 al 14 de Abril", 2026), "2026-04-14")

    def test_cierre_ausente(self):
        self.assertIsNone(scrape.parse_cierre("Texto sin fechas útiles", 2026))

    def test_profesorados(self):
        txt = ("Profesorado de Educación Superior en Historia Campo de Formación General. "
               "PROFESORADO EN EDUCACIÓN INICIAL para cubrir 3 horas")
        self.assertEqual(scrape.extract_profesorados(txt),
                         ["Profesorado de Educación Superior en Historia", "Profesorado en Educación Inicial"])

    def test_filtro_convocatorias(self):
        ok = scrape.is_convocatoria("1° CONVOCATORIA Prueba de Seleccion 2026", "https://x/a.pdf", CFG)
        no1 = scrape.is_convocatoria("Formulario 1 inscripcion anexo 9", "https://x/f.pdf", CFG)
        no2 = scrape.is_convocatoria("Res 6179 2025", "https://x/Res-6179-2025.pdf", CFG)
        self.assertTrue(ok)
        self.assertFalse(no1)
        self.assertFalse(no2)

    def test_fecha_desde_url(self):
        self.assertEqual(scrape.date_from_url(
            "https://buenosaires.gob.ar/sites/default/files/2025-09/dgsfd_20250903_ies1_x.pdf"), "2025-09-03")
        self.assertEqual(scrape.date_from_url(
            "https://isfd55-bue.infd.edu.ar/sitio/wp-content/uploads/2026/08/Conv.pdf"), "2026-08-01")


class Pipeline(unittest.TestCase):
    """Corrida completa con red simulada: sitio WordPress + página oficial de CABA."""

    def test_run(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp()) / "out.json"
        cfg = dict(CFG)
        cfg["sondeo_de_sitios"] = [{"patron": "https://isfd{n}-bue.infd.edu.ar", "desde": 55, "hasta": 56,
                                    "jurisdiccion": "PBA"}]
        cfg["sitios_extra"] = []
        cfg["paginas_html"] = []
        today = date.today().isoformat()

        class R:
            def __init__(self, data, url=""):
                self._d, self.url, self.status_code, self.text = data, url, 200, ""
            def json(self):
                return self._d

        def fake_get(url, **kw):
            if "isfd55" in url and url.endswith("/sitio/wp-json/"):
                return R({"name": "ISFD N° 55 - Escobar"}, url)
            if "isfd55" in url and "/wp-json/wp/v2/media" in url:
                return R([
                    {"source_url": "https://isfd55-bue.infd.edu.ar/sitio/wp-content/uploads/2026/08/Conv-4.pdf",
                     "title": {"rendered": "Convocatoria a Pruebas de Seleccion N 4"}, "date": today + "T10:00:00"},
                    {"source_url": "https://isfd55-bue.infd.edu.ar/sitio/wp-content/uploads/2026/08/Form.pdf",
                     "title": {"rendered": "Formulario inscripcion"}, "date": today + "T10:00:00"},
                ], url)
            return None

        info = {"cierre": "2099-01-10", "profesorados": ["Profesorado de Matematica"], "extracto": "x", "texto": "matematica"}
        with mock.patch.object(scrape, "http_get", side_effect=fake_get), \
             mock.patch.object(scrape, "analyze_pdf", return_value=info), \
             mock.patch.object(scrape, "OUT", tmp), \
             mock.patch.object(scrape, "CFG", Path(tmp.parent / "cfg.json")):
            (tmp.parent / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
            self.assertEqual(scrape.run(force=True), 0)
            data = json.loads(tmp.read_text(encoding="utf-8"))
            self.assertEqual(len(data["items"]), 1)
            self.assertEqual(data["items"][0]["instituto"], "ISFD N° 55 - Escobar")
            self.assertEqual(data["items"][0]["cierre"], "2099-01-10")
            # segunda corrida inmediata sin --force: no hace nada
            before = tmp.read_text(encoding="utf-8")
            self.assertEqual(scrape.run(force=False), 0)
            self.assertEqual(before, tmp.read_text(encoding="utf-8"))

    def test_sin_red_no_pisa_datos(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp()) / "out.json"
        tmp.write_text('{"actualizado": null, "items": [{"url":"u"}]}', encoding="utf-8")
        with mock.patch.object(scrape, "http_get", return_value=None), \
             mock.patch.object(scrape, "OUT", tmp):
            cfg_small = dict(CFG, sondeo_de_sitios=[], sitios_extra=[{"url": "https://x.invalid", "jurisdiccion": "PBA"}],
                             paginas_html=[])
            cfgp = Path(tmp.parent / "cfg.json"); cfgp.write_text(json.dumps(cfg_small), encoding="utf-8")
            with mock.patch.object(scrape, "CFG", cfgp):
                self.assertEqual(scrape.run(force=True), 2)
        self.assertIn('"url":"u"', tmp.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
