import base64
import io
import re
from datetime import date

import pytest

from src.cover_letter import CoverLetterGenerator
from src.database import BewerbungsDB
from src.kompetenzen import KompetenzenDB
from src.llm_client import LLMError
from src.resume_builder import ResumeBuilder, bilder_fuer_pdf, skill_keywords
from tests.conftest import FakeLLM


def test_skill_keywords():
    assert skill_keywords("Java 17+") == ["java"]
    assert skill_keywords("Scrum-Grundlagen") == ["scrum"]
    assert skill_keywords({"name": "X", "keywords": ["a", "b"]}) == ["a", "b"]


def test_lebenslauf_ersetzt_alle_marker(config):
    result = ResumeBuilder(config).anpassen_fuer_stelle(
        "Java Backend Developer, Spring Boot"
    )
    html = result["html"]
    assert ">alt<" not in html.replace("\n", "")
    assert "Junior Developer" in html
    assert result["berufsrichtung"] == "softwareentwicklung"


def test_fehlender_marker_wirft_fehler(config):
    builder = ResumeBuilder(config)
    with pytest.raises(ValueError):
        builder._replace_section("<html></html>", "SKILLS", "x")


def test_skills_relevante_kategorie_zuerst(config):
    html = ResumeBuilder(config)._baue_skills_html(
        "Linux-Administrator mit Windows, DNS"
    )
    assert html.index("Betriebssysteme") < html.index("Programmierung")


def test_skills_aus_kompetenz_db_werden_ergaenzt(config):
    db = KompetenzenDB(config.db_path)
    db.add_kompetenz("Docker", "ja", "selbstauskunft")
    db.add_kompetenz("Kubernetes", "teilweise", "selbstauskunft")
    db.add_kompetenz("AWS", "nein", "selbstauskunft")

    html = ResumeBuilder(config)._baue_skills_html("Docker, Kubernetes und AWS")
    assert "Weitere Kenntnisse" in html
    assert "Docker" in html and "Kubernetes (Grundlagen)" in html
    assert "AWS" not in html


def test_projekte_nach_tags(config):
    gewaehlt = ResumeBuilder(config).waehle_projekte("Linux und Bash Administration")
    ids = [p["id"] for p in gewaehlt]
    assert ids[0] == "linux"
    assert len(ids) == 2  # mindestens 2, aufgefüllt nach Reihenfolge


def test_projekte_bevorzugen_frueheren_erfolg(config):
    db = BewerbungsDB(config.db_path)
    bid = db.insert_bewerbung("Firma", projekte=["app"])
    db.add_rueckmeldung(bid, "einladung")

    # Ohne Tag-Treffer: Füll-Projekt ist das mit Erfolgs-Bonus
    ids = [p["id"] for p in ResumeBuilder(config).waehle_projekte("Kaufmann")]
    assert ids[0] == "app"


def test_anschreiben_ohne_ki_nutzt_fallback(config):
    projekte = ResumeBuilder(config).waehle_projekte("Java Spring")
    text = CoverLetterGenerator(config, None).generate(
        "Java", "Firma", "Dev", projekte
    )["text"]
    assert text.startswith("Anschreiben Dev bei Firma")
    assert "BackendApp" in text and "https://github.com/max" in text
    assert text.endswith("Max Muster")


def test_anschreiben_ki_fehler_nutzt_fallback(config):
    projekte = ResumeBuilder(config).waehle_projekte("Java")
    gen = CoverLetterGenerator(config, FakeLLM(fehler=LLMError("weg")))
    assert "Um meine Kenntnisse" in gen.generate("Java", "F", "D", projekte)["text"]


@pytest.mark.parametrize(
    "antwort",
    [
        "zu kurz",
        "Ich habe BackendApp gebaut. 这是中文的句子，模型切换了语言，所以不能用。" * 2,
        "Ich habe verschiedene Projekte umgesetzt und dabei sehr viel gelernt über Software.",
        "## BackendApp\n**Ich** habe BackendApp entwickelt und dabei sehr viel gelernt.",
    ],
)
def test_anschreiben_unbrauchbare_ki_antwort(config, antwort):
    projekte = ResumeBuilder(config).waehle_projekte("Java")
    gen = CoverLetterGenerator(config, FakeLLM(antwort))
    assert "Um meine Kenntnisse" in gen.generate("Java", "F", "D", projekte)["text"]


def test_anschreiben_gute_ki_antwort(config):
    projekte = ResumeBuilder(config).waehle_projekte("Java")
    antwort = "Mit BackendApp habe ich eine REST-API mit Java und Spring Boot gebaut und getestet."
    text = CoverLetterGenerator(config, FakeLLM(antwort)).generate(
        "Java", "F", "D", projekte
    )["text"]
    assert antwort in text
    assert "https://github.com/max" in text


def test_skills_gleichstand_bevorzugt_passendere_kategorie(config):
    # Je 2 Treffer: Betriebssysteme (2 von 2) vor Programmierung (2 von 3)
    html = ResumeBuilder(config)._baue_skills_html("Linux, Windows, Python und SQL")
    assert html.index("Betriebssysteme") < html.index("Programmierung")


def test_jahre_erfahrung_wird_eingesetzt(config):
    config.profil["berufserfahrung_seit"] = date.today().year - 8
    config.profil["anschreiben_vorlage"] = "vorlage_jahre.txt"
    (config.data_dir / "vorlage_jahre.txt").write_text(
        "Seit {jahre_erfahrung} Jahren {projekte_abschnitt}", encoding="utf-8"
    )
    builder = ResumeBuilder(config)
    builder.profiltexte["itsupport"]["profil"] = "{jahre_erfahrung}+ Jahre Erfahrung"
    html = builder.anpassen_fuer_stelle("Linux Support")["html"]
    assert "8+ Jahre Erfahrung" in html

    text = CoverLetterGenerator(config, None).generate("x", "F", "D")["text"]
    assert text.startswith("Seit 8 Jahren")


def test_jahre_erfahrung_fehlt_gibt_klare_meldung(config):
    (config.data_dir / "anschreiben_vorlage.txt").write_text(
        "Seit {jahre_erfahrung} Jahren", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="berufserfahrung_seit"):
        CoverLetterGenerator(config, None).generate("x", "F", "D")


def test_anschreiben_vorlage_nach_berufsrichtung(config):
    (config.data_dir / "support.txt").write_text(
        "Support-Anschreiben {position}", encoding="utf-8"
    )
    config.profil["anschreiben_vorlage"] = {
        "standard": "anschreiben_vorlage.txt",
        "itsupport": "support.txt",
    }
    gen = CoverLetterGenerator(config, None)
    support = gen.generate("x", "F", "Admin", berufsrichtung="itsupport")["text"]
    entwicklung = gen.generate("x", "F", "Dev", berufsrichtung="softwareentwicklung")
    assert support == "Support-Anschreiben Admin"
    assert entwicklung["text"].startswith("Anschreiben Dev bei F")


def test_foto_wird_als_baseline_jpeg_eingebettet(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", (2048, 2048), "red").save(
        tmp_path / "foto.jpg", "JPEG", progressive=True
    )
    html = '<img src="foto.jpg" alt="x"><img src="https://example.org/a.jpg">'
    neu = bilder_fuer_pdf(html, tmp_path)

    assert 'src="https://example.org/a.jpg"' in neu
    daten = re.search(r'src="data:image/jpeg;base64,([^"]+)"', neu).group(1)
    with Image.open(io.BytesIO(base64.b64decode(daten))) as bild:
        assert max(bild.size) <= 1000
        assert not bild.info.get("progressive")


def test_foto_fehlt_html_bleibt_unveraendert(tmp_path):
    html = '<img src="gibtsnicht.jpg">'
    assert bilder_fuer_pdf(html, tmp_path) == html
