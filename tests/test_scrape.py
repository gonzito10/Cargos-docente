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


class Cargos(unittest.TestCase):
    def test_cargos(self):
        txt = ("El ISFD N° 106 convoca a pruebas de selección para la cobertura de cargos de Jefe/a de Área "
               "de Formación General y Preceptor/a. A la Secretaría de Asuntos Docentes S/D. Director: Pérez")
        got = scrape.extract_cargos(txt)
        self.assertIn("Jefe/a de Área", got)
        self.assertIn("Preceptor/a", got)
        self.assertNotIn("Secretario/a", got)   # 'Secretaría de Asuntos Docentes' no es un cargo
        self.assertNotIn("Director/a", got)     # 'Director:' de la comisión, sin 'cargo' cerca

    def test_docente_y_espacios(self):
        txt = ("Convocatoria: cobertura de módulos y horas cátedra. REF 302 Tramo de Formación Pedagógica 3 hs "
               "REF 303 Didáctica de la Matemática 4 hs")
        self.assertIn("Docente (horas cátedra / módulos)", scrape.extract_cargos(txt))
        self.assertEqual(len(scrape.extract_espacios(txt)), 2)
        self.assertTrue(scrape.extract_detalle(txt).startswith("módulos y horas cátedra"))

    def test_drive_url(self):
        self.assertEqual(scrape.drive_download_url("https://drive.google.com/file/d/AbC_1-x/view?usp=sharing"),
                         "https://drive.google.com/uc?export=download&id=AbC_1-x")


class SadPage(unittest.TestCase):
    def test_tabla_comunicados(self):
        html_ = """<table>
        <tr><td>816</td><td>05/11/2026</td><td>Convocatoria ISFT N° 206</td><td><a href="https://drive.google.com/file/d/AAA/view">2026_S2_816</a></td></tr>
        <tr><td>813</td><td>05/11/2026</td><td>Convocatoria ISFD N° 106</td><td><a href="https://drive.google.com/file/d/BBB/view">2026_S2_813</a></td></tr>
        <tr><td>809</td><td>03/11/2026</td><td>Convocatoria a Acto Público Cargos Jerárquicos Educación Secundaria Distrito de Morón</td><td><a href="https://drive.google.com/file/d/CCC/view">2026_S2_809</a></td></tr>
        <tr><td>802</td><td>02/11/2026</td><td>Convocatoria ISFD y T N° 105</td><td><a href="https://drive.google.com/file/d/DDD/view">2026_S2_802</a></td></tr>
        <tr><td>800</td><td>02/11/2026</td><td>Aviso de mesa de examen ISFD N° 106</td><td><a href="https://drive.google.com/file/d/EEE/view">2026_S2_800</a></td></tr>
        </table>"""

        class R:
            text, url, status_code = html_, "https://sadmatanza.com/secretaria2/Comunicados2026.html", 200

        src = {"nombre": "SAD La Matanza", "url": "https://sadmatanza.com/secretaria2/Comunicados{anio}.html"}
        with mock.patch.object(scrape, "http_get", return_value=R()):
            got = scrape.scrape_sad_page(src, CFG, date(2026, 1, 1), date(2026, 11, 9))
        self.assertEqual([g["instituto"] for g in got], ["ISFD N° 106", "ISFDyT N° 105"])
        self.assertEqual(got[0]["fecha"], "2026-11-05")
        self.assertEqual(got[0]["titulo"], "Convocatoria ISFD N° 106")
        self.assertEqual(got[0]["fuente"], "SAD La Matanza")


class CabaIndex(unittest.TestCase):
    def test_indice_e_instituto(self):
        index = ('<a href="/educacion/estudiantes/formacion-docente/cobertura-de-catedras/ens-6">ENS N° 6 "Vicente López"</a>'
                 '<a href="/gcaba_historico/cobertura-de-catedras">volver</a>'
                 '<a href="https://otro.gob.ar/cobertura-de-catedras/x">externo</a>')
        inst = ('<p><strong>Actualizado: 04/11/2026</strong></p>'
                '<a href="/sites/default/files/2026-11/PEI-Musica.pdf">PEI CFE - Música en la Educación Inicial</a>'
                '<p><strong>Actualizado: 01/01/2020</strong></p>'
                '<a href="/sites/default/files/2020-01/viejo.pdf">Viejo</a>')

        class R:
            def __init__(self, text, url):
                self.text, self.url, self.status_code = text, url, 200

        def fake_get(url, **kw):
            if url.endswith("/gcaba_historico/cobertura-de-catedras"):
                return R(index, "https://buenosaires.gob.ar/gcaba_historico/cobertura-de-catedras")
            if url.endswith("/cobertura-de-catedras/ens-6"):
                return R(inst, url)
            return None

        page = {"url": "https://buenosaires.gob.ar/gcaba_historico/cobertura-de-catedras",
                "indice_institutos": True, "jurisdiccion": "CABA"}
        with mock.patch.object(scrape, "http_get", side_effect=fake_get):
            got = scrape.scrape_html_page(page, CFG, date(2026, 1, 1))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["fecha"], "2026-11-04")
        self.assertIn("Vicente", got[0]["instituto"])
        self.assertEqual(got[0]["titulo"], "PEI CFE - Música en la Educación Inicial")


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
