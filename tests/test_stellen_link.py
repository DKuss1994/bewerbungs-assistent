import json

from src.stellen_info import erkenne_stelle
from src.stellen_link import anzeige_aus_html, html_zu_text

JOBPOSTING = {
    "@context": "https://schema.org",
    "@graph": [
        {"@type": "WebPage", "name": "Jobs"},
        {
            "@type": "JobPosting",
            "title": "IT-Support (m/w/d)",
            "hiringOrganization": {"@type": "Organization", "name": "Muster IT GmbH"},
            "description": "<p>Du betreust <b>Linux</b>-Systeme.</p>"
            "<ul><li>Bewerbung an jobs@muster-it.de</li></ul>",
        },
    ],
}


def test_jobposting_aus_json_ld():
    seite = (
        "<html><head><script type='application/ld+json'>"
        f"{json.dumps(JOBPOSTING)}</script></head>"
        "<body><nav>Menü</nav><p>Cookie-Banner</p></body></html>"
    )
    text = anzeige_aus_html(seite)

    assert "Cookie" not in text and "Du betreust Linux-Systeme." in text
    info = erkenne_stelle(text)
    assert info.unternehmen == "Muster IT GmbH"
    assert info.position == "IT-Support"
    assert info.email == "jobs@muster-it.de"


def test_ohne_json_ld_sichtbarer_text():
    seite = (
        "<html><head><title>x</title><style>p{}</style></head><body>"
        "<nav>Startseite</nav><h1>Admin (m/w/d)</h1><p>Wir&nbsp;suchen   dich.</p>"
        "<script>var a = 1;</script><footer>Impressum</footer></body></html>"
    )
    assert anzeige_aus_html(seite) == "Admin (m/w/d)\nWir suchen dich."


def test_html_zu_text_kaputtes_json_wird_uebersprungen():
    seite = "<script type='application/ld+json'>{kaputt</script><p>Text</p>"
    assert anzeige_aus_html(seite) == "Text"
    assert html_zu_text("<b>a</b><br>b") == "a\nb"
