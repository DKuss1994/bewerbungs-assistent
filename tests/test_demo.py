from src import demo


def test_demo_laeuft_durch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(demo, "DEMO_DIR", tmp_path / "demo")
    # PDFs brauchen WeasyPrint – im Test reicht das HTML
    monkeypatch.setattr(demo.ResumeBuilder, "speichere_pdf", lambda self, html, pfad: 1)
    demo.main()
    ausgabe = capsys.readouterr().out
    assert "Datenwerk Nord GmbH" in ausgabe
    assert "einladung" in ausgabe and "fehlende_erfahrung" in ausgabe
    assert "Anruf fällig" in ausgabe
    assert "BEWERBUNGS-REPORT" in ausgabe
