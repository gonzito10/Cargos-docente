#!/usr/bin/env python3
"""Recolector de convocatorias docentes en profesorados (Provincia de Buenos Aires y CABA).

Recorre los sitios de los institutos (WordPress de infd.edu.ar), detecta PDFs de
convocatorias / selecciones de antecedentes, extrae datos básicos y escribe
docs/data/convocatorias.json, que consume la web estática.

Uso:
    python scraper/scrape.py            # respeta el intervalo mínimo (3 días)
    python scraper/scrape.py --force    # corre siempre
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from pypdf import PdfReader
except ImportError:  # el recolector sigue funcionando sin análisis de PDF
    PdfReader = None

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "docs" / "data" / "convocatorias.json"
CFG = HERE / "sources.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; BuscadorCargosDocentes/1.0)"}
TIMEOUT = 15
MAX_PDF_BYTES = 8 * 1024 * 1024

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
_M = "|".join(MESES)


# --------------------------------------------------------------------------- utilidades
def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def http_get(url: str, timeout: int = TIMEOUT, **kw):
    try:
        r = requests.get(url, headers=UA, timeout=timeout, **kw)
        return r if r.status_code == 200 else None
    except requests.RequestException:
        return None


def clean_title(raw: str, url: str) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", "", raw or "")).strip()
    if not t:
        t = re.sub(r"[-_]+", " ", unquote(Path(urlparse(url).path).stem))
    t = re.sub(r"\s+", " ", t).strip()
    return t


def is_convocatoria(title: str, url: str, cfg: dict) -> bool:
    hay = norm(title + " " + unquote(Path(urlparse(url).path).name))
    if not any(w in hay for w in cfg["palabras_incluir"]):
        return False
    return not any(w in hay for w in cfg["palabras_excluir"])


def date_from_url(url: str) -> str | None:
    """Fecha aproximada desde /uploads/AAAA/MM/ o desde dgsfd_AAAAMMDD_ en el nombre."""
    name = unquote(Path(urlparse(url).path).name)
    m = re.search(r"(?:^|[_-])(20\d{2})(\d{2})(\d{2})(?:[_-]|$)", name)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            pass
    m = re.search(r"/uploads/(20\d{2})/(\d{2})/", url)
    if m:
        return f"{m[1]}-{m[2]}-01"
    return None


# --------------------------------------------------------------------------- análisis de texto
DATE_RANGE = re.compile(
    rf"(\d{{1,2}})\s*(?:de\s+)?(?:{_M})?\s*(?:al|a|hasta(?:\s+el)?)\s*(\d{{1,2}})\s*de\s+({_M})"
    rf"(?:\s*(?:de|del)?\s*(\d{{4}}))?",
    re.I,
)
DATE_UNTIL = re.compile(
    rf"hasta\s+(?:el\s+)?(\d{{1,2}})\s*de\s+({_M})(?:\s*(?:de|del)?\s*(\d{{4}}))?", re.I
)


def parse_cierre(text: str, default_year: int) -> str | None:
    """Intenta hallar la fecha de cierre de inscripción en el texto de la convocatoria."""
    t = norm(text)
    windows = [t[m.start(): m.start() + 300] for m in re.finditer(r"inscripci", t)]
    windows.append(t)  # último recurso: todo el texto
    for w in windows:
        for rx, day_g, month_g, year_g in ((DATE_RANGE, 2, 3, 4), (DATE_UNTIL, 1, 2, 3)):
            m = rx.search(w)
            if not m:
                continue
            try:
                year = int(m.group(year_g)) if m.group(year_g) else default_year
                return date(year, MESES[m.group(month_g).lower()], int(m.group(day_g))).isoformat()
            except (ValueError, KeyError):
                continue
    return None


AL = "A-Za-zÁÉÍÓÚÜÑáéíóúüñ"
PROF_RX = re.compile(
    rf"profesorado\s+(?:universitario\s+|superior\s+)?(?:de|en)\s+(?:educaci[oó]n\s+)?[{AL} ]{{3,60}}",
    re.I,
)
STOP_RX = re.compile(
    r"\s+(?:campo|c[aá]tedra|unidad|para|con|m[oó]dulo|hora|turno|resoluci[oó]n|res|ciclo|"
    r"instituto|tramo|anual|cuatrim)\b",
    re.I,
)


def extract_profesorados(text: str) -> list[str]:
    found: list[str] = []
    for m in PROF_RX.finditer(text):
        s = m.group(0)
        cut = STOP_RX.search(s)
        if cut:
            s = s[: cut.start()]
        s = re.sub(r"\s+", " ", s).strip(" .,-")
        if s.isupper():
            s = s.title().replace(" De ", " de ").replace(" En ", " en ").replace(" Y ", " y ")
        if len(s) > 14 and s not in found:
            found.append(s)
    return found[:5]


def analyze_pdf(url: str, default_year: int) -> dict:
    out = {"cierre": None, "profesorados": [], "extracto": "", "texto": ""}
    if PdfReader is None:
        return out
    r = http_get(url, timeout=30, stream=True)
    if r is None:
        return out
    try:
        buf = io.BytesIO()
        for chunk in r.iter_content(65536):
            buf.write(chunk)
            if buf.tell() > MAX_PDF_BYTES:
                return out
        reader = PdfReader(buf)
        text = " ".join((p.extract_text() or "") for p in reader.pages[:3])
    except Exception:
        return out
    text = re.sub(r"\s+", " ", text).strip()
    out["texto"] = text[:1200]
    out["extracto"] = text[:320]
    out["cierre"] = parse_cierre(text, default_year)
    out["profesorados"] = extract_profesorados(text)
    return out


# --------------------------------------------------------------------------- recolección por sitio
def detect_site(base: str):
    """Devuelve (prefijo, nombre, tiene_api) o None si el sitio no responde."""
    for prefix in ("/sitio", ""):
        r = http_get(f"{base}{prefix}/wp-json/", timeout=10)
        if r is not None:
            try:
                return prefix, r.json().get("name") or "", True
            except ValueError:
                pass
    r = http_get(f"{base}/sitio/", timeout=10)
    if r is not None:
        title = BeautifulSoup(r.text, "html.parser").title
        return "/sitio", (title.get_text(strip=True) if title else ""), False
    return None


def site_media(base: str, prefix: str, cutoff: date) -> list[dict]:
    items: list[dict] = []
    for page in (1, 2):
        r = http_get(
            f"{base}{prefix}/wp-json/wp/v2/media",
            params={"per_page": 100, "page": page, "orderby": "date", "order": "desc",
                    "mime_type": "application/pdf"},
        )
        if r is None:
            break
        try:
            batch = r.json()
        except ValueError:
            break
        if not isinstance(batch, list) or not batch:
            break
        for m in batch:
            items.append({
                "url": m.get("source_url", ""),
                "titulo": (m.get("title") or {}).get("rendered", ""),
                "fecha": (m.get("date") or "")[:10] or None,
            })
        oldest = items[-1]["fecha"]
        if len(batch) < 100 or (oldest and oldest < cutoff.isoformat()):
            break
    return items


def site_html_fallback(base: str, prefix: str) -> list[dict]:
    """Si la API REST está cerrada: lee la home y las páginas de convocatorias."""
    home = http_get(f"{base}{prefix}/")
    if home is None:
        return []
    soup = BeautifulSoup(home.text, "html.parser")
    pages = [f"{base}{prefix}/"]
    for a in soup.find_all("a", href=True):
        label = norm(a.get_text(" ") + " " + a["href"])
        if re.search(r"convocatoria|seleccion|concurso|cargos|docentes", label):
            u = urljoin(home.url, a["href"])
            if urlparse(u).netloc == urlparse(base).netloc and u not in pages:
                pages.append(u)
    found: dict[str, dict] = {}
    for p in pages[:7]:
        r = home if p == pages[0] else http_get(p)
        if r is None:
            continue
        for a in BeautifulSoup(r.text, "html.parser").find_all("a", href=True):
            u = urljoin(r.url, a["href"])
            if u.lower().split("?")[0].endswith(".pdf"):
                found.setdefault(u, {"url": u, "titulo": a.get_text(" ", strip=True), "fecha": None})
    return list(found.values())


def scrape_site(base: str, jur: str, cfg: dict, cutoff: date) -> tuple[str, list[dict]] | None:
    det = detect_site(base)
    if det is None:
        return None
    prefix, name, has_api = det
    raw = site_media(base, prefix, cutoff) if has_api else []
    if not raw:
        raw = site_html_fallback(base, prefix)
    label = re.sub(r"\s+", " ", html.unescape(name)).strip()
    code = urlparse(base).netloc.split(".")[0].split("-")[0].upper()  # ISFD55, ENS4, IES1…
    if not label:
        label = code
    elif not re.search(r"\d", label):
        label = f"{label} ({code})"
    out = []
    for it in raw:
        if not it["url"].lower().split("?")[0].endswith(".pdf"):
            continue
        title = clean_title(it["titulo"], it["url"])
        if not is_convocatoria(title, it["url"], cfg):
            continue
        out.append({
            "url": it["url"], "titulo": title, "instituto": label, "jurisdiccion": jur,
            "fecha": it["fecha"] or date_from_url(it["url"]), "sitio": base,
        })
    return label, out


ACTUALIZADO_RX = re.compile(r"actualiza\w*\s*:?\s*(\d{1,2})/(\d{1,2})/(\d{2,4})", re.I)


def _iso(d: str, m: str, y: str) -> str | None:
    year = int(y) + (2000 if len(y) == 2 else 0)
    try:
        return date(year, int(m), int(d)).isoformat()
    except ValueError:
        return None


def scrape_institute_page(url: str, inst: str, jur: str, cutoff: date) -> list[dict]:
    """Página de un instituto en buenosaires.gob.ar: PDFs agrupados bajo 'Actualizado: DD/MM/AAAA'."""
    r = http_get(url)
    if r is None:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    current: str | None = None
    out, seen = [], set()
    for el in soup.find_all(["strong", "b", "h2", "h3", "h4", "p", "span", "a"]):
        if el.name == "a":
            u = urljoin(r.url, el.get("href", ""))
            if not u.lower().split("?")[0].endswith(".pdf") or u in seen:
                continue
            fecha = current or date_from_url(u)
            if fecha is None or fecha < cutoff.isoformat():
                continue
            seen.add(u)
            out.append({"url": u, "titulo": clean_title(el.get_text(" ", strip=True), u),
                        "instituto": inst, "jurisdiccion": jur, "fecha": fecha, "sitio": url})
        else:
            txt = el.get_text(" ", strip=True)
            if len(txt) < 60 and not el.find("a"):
                m = ACTUALIZADO_RX.search(txt)
                if m:
                    current = _iso(*m.groups()) or current
    return out


def scrape_html_page(page: dict, cfg: dict, cutoff: date) -> list[dict]:
    """Páginas oficiales de CABA. Dos modos:
    - indice_institutos: el índice enlaza a una página por instituto (todos sus PDFs son llamados).
    - seguir_enlace_con_texto: sigue un enlace y junta los PDFs que parecen convocatorias."""
    r = http_get(page["url"])
    if r is None:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    if page.get("indice_institutos"):
        base_path = urlparse(r.url).path.rstrip("/")
        targets: dict[str, tuple[str, str]] = {}
        for a in soup.find_all("a", href=True):
            u = urljoin(r.url, a["href"]).split("#")[0].rstrip("/")
            pu = urlparse(u)
            if pu.netloc != urlparse(r.url).netloc or "cobertura-de-catedras/" not in pu.path:
                continue
            if pu.path.rstrip("/") == base_path:
                continue
            slug = pu.path.rsplit("/", 1)[-1]
            name = re.sub(r"\s+", " ", html.unescape(a.get_text(" ", strip=True)))
            if name and (slug not in targets or "/educacion/estudiantes/" in pu.path):
                targets[slug] = (u, name)
        out: list[dict] = []
        with ThreadPoolExecutor(max_workers=6) as ex:
            for items in ex.map(lambda t: scrape_institute_page(t[0], t[1], page["jurisdiccion"], cutoff),
                                targets.values()):
                out += items
        return out

    target = r
    want = norm(page.get("seguir_enlace_con_texto", ""))
    if want:
        for a in soup.find_all("a", href=True):
            if want in norm(a.get_text(" ")):
                nxt = http_get(urljoin(r.url, a["href"]))
                if nxt is not None:
                    target = nxt
                break
    out = []
    for a in BeautifulSoup(target.text, "html.parser").find_all("a", href=True):
        u = urljoin(target.url, a["href"])
        if not u.lower().split("?")[0].endswith(".pdf"):
            continue
        title = clean_title(a.get_text(" ", strip=True), u)
        if not is_convocatoria(title, u, cfg):
            continue
        m = re.search(r"dgsfd_\d{8}_([a-z0-9 %]+?)_", unquote(u), re.I)
        inst = re.sub(r"\s+", " ", m.group(1)).upper() if m else "CABA – Formación Docente"
        out.append({"url": u, "titulo": title, "instituto": inst, "jurisdiccion": page["jurisdiccion"],
                    "fecha": date_from_url(u), "sitio": page["url"]})
    return out


# --------------------------------------------------------------------------- principal
def load_previous() -> dict:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run(force: bool) -> int:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    today = now.date()
    prev = load_previous()

    if not force and prev.get("actualizado"):
        last = datetime.fromisoformat(prev["actualizado"])
        min_gap = timedelta(days=cfg["dias_minimos_entre_corridas"]) - timedelta(hours=3)
        if now - last < min_gap:
            print(f"Última actualización {last:%Y-%m-%d %H:%M} UTC: todavía no pasaron "
                  f"{cfg['dias_minimos_entre_corridas']} días. Nada que hacer.")
            return 0

    cutoff = today - timedelta(days=cfg["max_antiguedad_dias"])
    sites = [(p["patron"].format(n=n), p["jurisdiccion"])
             for p in cfg["sondeo_de_sitios"] for n in range(p["desde"], p["hasta"] + 1)]
    sites += [(s["url"], s["jurisdiccion"]) for s in cfg.get("sitios_extra", [])]

    found: list[dict] = []
    alive = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        for res in ex.map(lambda s: scrape_site(s[0], s[1], cfg, cutoff), sites):
            if res is not None:
                alive += 1
                found += res[1]
        for page_items in ex.map(lambda p: scrape_html_page(p, cfg, cutoff), cfg.get("paginas_html", [])):
            found += page_items

    print(f"Sitios que respondieron: {alive}/{len(sites)} · PDFs candidatos: {len(found)}")
    if alive == 0 and not found:
        print("ERROR: ninguna fuente respondió (¿sin red?). No se modifica el archivo de datos.")
        return 2

    old = {i["url"]: i for i in prev.get("items", [])}
    merged: dict[str, dict] = {}
    for it in found:
        if it["url"] in merged:
            continue
        fecha = it["fecha"] or today.isoformat()
        if fecha < cutoff.isoformat():
            continue
        prior = old.get(it["url"])
        if prior and "texto" in prior:
            info = {k: prior.get(k) for k in ("cierre", "profesorados", "extracto", "texto")}
            first = prior.get("primera_vez", today.isoformat())
        else:
            info = analyze_pdf(it["url"], int(fecha[:4]))
            first = today.isoformat()
        merged[it["url"]] = {
            "id": hashlib.sha1(it["url"].encode()).hexdigest()[:12],
            **{k: it[k] for k in ("url", "titulo", "instituto", "jurisdiccion", "sitio")},
            "fecha": fecha, **info, "primera_vez": first, "ultima_vez": today.isoformat(),
        }
    # conservar lo que ya teníamos y sigue dentro del plazo (el sitio pudo reordenar/quitar archivos)
    for url, prior in old.items():
        if url not in merged and prior.get("fecha", "") >= cutoff.isoformat():
            merged[url] = prior

    items = sorted(merged.values(), key=lambda i: (i["fecha"], i["primera_vez"]), reverse=True)
    items = items[: cfg["max_items"]]
    payload = {
        "actualizado": now.isoformat(timespec="seconds"),
        "proxima_actualizacion": (now + timedelta(days=cfg["dias_minimos_entre_corridas"]))
        .isoformat(timespec="seconds"),
        "sitios_consultados": len(sites),
        "sitios_con_respuesta": alive,
        "items": items,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    nuevos = sum(1 for i in items if i["primera_vez"] == today.isoformat())
    print(f"Guardado: {len(items)} convocatorias ({nuevos} nuevas) → {OUT}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="ignora el intervalo mínimo")
    sys.exit(run(ap.parse_args().force))
