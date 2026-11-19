import json

import pytest

from src.config import AppConfig
from src.cover_letter import CoverLetterGenerator
from src.einrichtung import lege_an, main, pruefe
from src.resume_builder import ResumeBuilder
from tests.conftest import FakeDialog


def test_neue_einrichtung_ergibt_lauffaehige_unterlagen(tmp_path):
    (tmp_path / ".env.example").write_text("EMAIL_ABSENDER=\n")
    config = AppConfig(base_dir=tmp_path)

    assert lege_an(config, FakeDialog("Erika Muster", "", "2015", "12345"))

    profil = json.loads((config.data_dir / "profil.json").read_text())
    assert profil["name"] == "Erika Muster" and profil["berufserfahrung_seit"] == 2015
    assert (tmp_path / ".env").exists()
    kriterien = json.loads((config.data_dir / "kriterien.json").read_text())
    assert kriterien["plz"] == "12345" and "IT-Support" in kriterien["suchbegriffe"]
    # die Vorlagen passen zu Lebenslauf- und Anschreiben-Generator
    resume = ResumeBuilder(config).anpassen_fuer_stelle("Python und SQL")
    assert "Erika Muster" in resume["html"] and "Beispielprojekt" in resume["html"]
    text = CoverLetterGenerator(config, None).generate(
        "Python", "Firma GmbH", "Entwickler", projekte=resume["projekte"]
    )["text"]
    assert "Bewerbung als Entwickler" in text


def test_vorhandenes_wird_nicht_ueberschrieben(config):
    (config.data_dir / "kriterien.json").write_text('{"plz": "12345"}')
    vorher = (config.data_dir / "profil.json").read_text()
    dialog = FakeDialog()  # keine Fragen, weil profil.json schon da ist
    assert lege_an(config, dialog)
    assert (config.data_dir / "profil.json").read_text() == vorher
    assert dialog.fragen == []
    # die Lebenslauf-Vorlage heißt wie in profil.json – keine zweite daneben
    assert sorted(p.name for p in (config.data_dir / "lebenslauf").iterdir()) == [
        "lebenslauf.html"
    ]


def test_eigener_lebenslauf_name_wird_beachtet(config):
    profil = json.loads((config.data_dir / "profil.json").read_text())
    (config.data_dir / "lebenslauf" / "lebenslauf.html").rename(
        config.data_dir / "lebenslauf" / "ich.html"
    )
    profil["lebenslauf_html"] = "ich.html"
    (config.data_dir / "profil.json").write_text(json.dumps(profil))
    lege_an(AppConfig(base_dir=config.base_dir), FakeDialog(""))
    assert not (config.data_dir / "lebenslauf" / "lebenslauf.html").exists()


def test_ohne_plz_keine_stellensuche(config):
    for antwort in ("", "abc", None):
        lege_an(config, FakeDialog(antwort))
        assert not (config.data_dir / "kriterien.json").exists()


def test_pruefen_zeigt_was_fehlt(config, monkeypatch):
    for name in ("EMAIL_ABSENDER", "TELEGRAM_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("src.einrichtung._ki_erreichbar", lambda c: False)
    zeilen = "\n".join(pruefe(config))
    assert "✅ profil.json" in zeilen
    assert "⚠️ Stellensuche (optional)" in zeilen
    assert "⚠️ E-Mail-Versand (SMTP)" in zeilen and "⚠️ 0 PDF(s)" in zeilen


def test_ohne_namen_abbruch(tmp_path):
    with pytest.raises(SystemExit):
        main([], AppConfig(base_dir=tmp_path), FakeDialog(""))
