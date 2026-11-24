"""The Schwab page logic, in a real Chromium, on stand-in pages.

The gateway's answers here are stand-ins shaped like the ones PaperPull's
Schwab app reads. Every account number, id and date is invented."""
import base64
import json
from pathlib import Path
from urllib.parse import urlsplit
import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1] / "extension"
HOST = "https://client.schwab.com"
GATEWAY = "https://ausgateway.schwab.com/api/is.StatementsWeb/StatementsInterface/Statements"
PAGE = "/retail/accounts/statements/"
PDF = b"%PDF-1.4 stand-in"
CORS = {"access-control-allow-origin": HOST, "access-control-allow-headers": "*",
        "access-control-allow-methods": "GET, POST"}

ACCOUNTS = {"accountSelectorData": {"brokerageAccountList": {
    "brokerageAccounts": [{"id": "11111111", "nickName": "Brokerage"}],
    "viewOnlyAccounts": [{"id": "12345678", "isCharitable": True}],
}}}


def row(doc_id, date="10/31/2026", name="Brokerage Statement", account="...1111", kind="Statements"):
    return {"documentId": doc_id, "documentName": name, "date": date, "type": kind,
            "accountDisplayName": account, "onDemandDocumentType": ""}


@pytest.fixture(scope="module")
def tab():
    with sync_playwright() as p:
        b = p.chromium.launch()
        t = b.new_page()
        yield t
        b.close()


def load(tab, url, body, title="Statements &amp; Tax Forms", token="stand-in-token",
         documents=None, accounts=ACCOUNTS, extra=None, download=PDF):
    """A stand-in page at url, with a stand-in token endpoint and gateway.
    Answers the list of every request made after the page loaded."""
    asked = []

    def route(r):
        req = r.request
        asked.append({"url": req.url, "method": req.method, "headers": req.headers,
                      "body": req.post_data})
        parts = urlsplit(req.url)
        if req.method == "OPTIONS":
            return r.fulfill(status=204, headers=CORS)
        if parts.hostname == "ausgateway.schwab.com":
            if req.headers.get("authorization") != f"Bearer {token}":
                return r.fulfill(status=401, headers=CORS, body="")
            if parts.path.endswith("/accounts"):
                return r.fulfill(status=200, headers=CORS, content_type="application/json", body=json.dumps(accounts))
            if parts.path.endswith("/brokerage/documents"):
                answer = {"documents": documents if documents is not None else [], **(extra or {})}
                return r.fulfill(status=200, headers=CORS, content_type="application/json", body=json.dumps(answer))
            if parts.path.endswith("/download"):
                return r.fulfill(status=200, headers=CORS, content_type="application/pdf", body=download)
            return r.fulfill(status=404, headers=CORS, body="")
        if parts.path == "/api/auth/authorize/scope/api":
            return r.fulfill(status=200, content_type="application/json",
                             body=json.dumps({"token": token} if token else {}))
        r.fulfill(status=200, content_type="text/html",
                  body=f"<!doctype html><title>{title}</title><body>{body}</body>")

    tab.unroute("**/*")
    tab.route("**/*", route)
    tab.goto(url)
    for f in ("shared.js", "schwab.page.js"):
        tab.add_script_tag(content=(ROOT / "providers" / f).read_text())
    asked.clear()
    return asked


def read(tab):
    return tab.evaluate("__sweep.readList()")


@pytest.mark.parametrize("url", ["/Areas/Access/Login", "/x?SessionTimeout=true"])
def test_a_sign_in_address_is_known(tab, url):
    load(tab, HOST + url, "<p>Sign in</p>", title="Login")
    assert read(tab) == {"state": "signed_out"}


def test_a_password_box_is_a_sign_in(tab):
    load(tab, HOST + PAGE, "<input type=password>")
    assert read(tab) == {"state": "signed_out"}


def test_a_sign_in_address_in_a_query_is_not_the_statements_page(tab):
    load(tab, HOST + "/other?next=/retail/accounts/statements/", "<p>Hello</p>", title="Summary")
    assert read(tab) == {"state": "no_list"}


@pytest.mark.parametrize("words", ["Enter your verification code", "You've been logged out"])
def test_a_security_check_is_known(tab, words):
    load(tab, HOST + "/app/accounts/summary/", f"<p>{words}</p>", title="Schwab")
    assert read(tab) == {"state": "challenge"}


def test_throttling_stops(tab):
    load(tab, HOST + "/app/accounts/summary/", "<p>Service temporarily unavailable</p>", title="Schwab")
    assert read(tab)["state"] == "stopped"


def test_another_page_is_not_the_list_and_asks_nothing(tab):
    asked = load(tab, HOST + "/app/accounts/summary/", "<p>Accounts</p>", title="Summary")
    assert read(tab) == {"state": "no_list"}
    assert asked == []


@pytest.mark.parametrize("path", [PAGE, "/app/accounts/statements/"])
def test_the_list_is_read_from_the_gateway_newest_first(tab, path):
    docs = [row("a1", "09/30/2026"), row("a2", "10/31/2026"),
            row("t1", "02/14/2026", name="1099 Composite", account="...5678", kind="Tax Forms")]
    asked = load(tab, HOST + path, "<button aria-label='Click to Download PDF'>x</button>", documents=docs)
    got = read(tab)
    assert got["state"] == "ok"
    assert [d["date"] for d in got["docs"]] == ["2026-10-31", "2026-09-30", "2026-02-14"]
    assert got["docs"][0]["title"] == "Brokerage Statement ...1111"
    assert got["docs"][2]["title"] == "1099 Composite ...5678"
    assert all(d["method"] == "fetch" for d in got["docs"])
    hosts = {urlsplit(a["url"]).hostname for a in asked}
    assert hosts <= {"client.schwab.com", "ausgateway.schwab.com"}
    listed = next(a for a in asked if "/brokerage/documents" in a["url"] and a["method"] == "GET")
    assert listed["headers"]["schwab-client-ids"] == "11111111"
    assert listed["headers"]["schwab-client-charitable-ids"] == "12345678"


def test_a_documents_id_stays_when_schwab_renumbers_it(tab):
    load(tab, HOST + PAGE, "", documents=[row("first-id")])
    before = read(tab)["docs"][0]["id"]
    load(tab, HOST + PAGE, "", documents=[row("other-id")])
    after = read(tab)["docs"][0]
    assert isinstance(before, str) and before and before == after["id"]
    assert after["documentId"] == "other-id"


def test_two_documents_alike_on_one_day_keep_apart(tab):
    load(tab, HOST + PAGE, "", documents=[row("x1"), row("x2")])
    ids = [d["id"] for d in read(tab)["docs"]]
    assert len(set(ids)) == 2


def test_documents_of_two_accounts_keep_apart(tab):
    load(tab, HOST + PAGE, "", documents=[row("x1"), row("x2", account="...5678")])
    ids = [d["id"] for d in read(tab)["docs"]]
    assert len(set(ids)) == 2


def test_an_id_holds_no_whole_account_number(tab):
    load(tab, HOST + PAGE, "", documents=[row("x1", account="Brokerage 11111111")])
    doc = read(tab)["docs"][0]
    assert "11111111" not in doc["id"] and "11111111" not in doc["title"]


def test_no_token_is_a_sign_in(tab):
    load(tab, HOST + PAGE, "", token="")
    assert read(tab) == {"state": "signed_out"}


def test_an_undated_document_stops_and_keeps_the_rest(tab):
    load(tab, HOST + PAGE, "", documents=[row("a1"), row("a2", date="soon")])
    got = read(tab)
    assert got["state"] == "stopped" and len(got["docs"]) == 1 and "date" in got["why"]


def test_a_day_that_does_not_exist_is_not_a_date(tab):
    load(tab, HOST + PAGE, "", documents=[row("a1", date="02/30/2026")])
    assert read(tab)["state"] == "stopped"


def test_a_partial_list_stops(tab):
    load(tab, HOST + PAGE, "", documents=[row("a1")], extra={"charitableSuccess": False})
    got = read(tab)
    assert got["state"] == "stopped" and len(got["docs"]) == 1


def test_no_accounts_stops(tab):
    load(tab, HOST + PAGE, "", accounts={"accountSelectorData": {"brokerageAccountList": {}}})
    assert read(tab)["state"] == "stopped"


def test_a_title_drawn_late_is_waited_for(tab):
    load(tab, HOST + PAGE, "<script>setTimeout(() => { document.title = 'Statements'; }, 400)</script>",
         title="Loading", documents=[row("a1")])
    assert read(tab)["state"] == "ok"


def test_a_pdf_is_fetched_by_the_pages_own_download(tab):
    asked = load(tab, HOST + PAGE, "", documents=[row("a1")])
    doc = read(tab)["docs"][0]
    asked.clear()
    got = tab.evaluate("(d) => __sweep.fetchPdf(d)", doc)
    assert got["ok"] and base64.b64decode(got["base64"]) == PDF
    post = next(a for a in asked if a["method"] == "POST")
    assert post["url"] == GATEWAY + "/download"
    assert json.loads(post["body"]) == {"documentId": "a1", "documentName": "Brokerage Statement",
                                        "documentType": "Statements", "downloadFileType": "PDF"}


def test_bytes_that_are_not_a_pdf_are_refused(tab):
    load(tab, HOST + PAGE, "", documents=[row("a1")], download=b"<html>nope</html>")
    doc = read(tab)["docs"][0]
    assert tab.evaluate("(d) => __sweep.fetchPdf(d)", doc)["ok"] is False


def test_a_doc_with_no_document_id_is_not_fetched(tab):
    asked = load(tab, HOST + PAGE, "")
    got = tab.evaluate("() => __sweep.fetchPdf({id: 'x', date: '2026-10-01', method: 'fetch', url: '/danger/doc'})")
    assert got["ok"] is False and asked == []


def test_it_presses_nothing_on_the_page(tab):
    load(tab, HOST + PAGE, "<button aria-label='Click to Download PDF' "
                           "onclick=\"window.pressed=true\">x</button>", documents=[row("a1")])
    doc = read(tab)["docs"][0]
    tab.evaluate("(d) => __sweep.fetchPdf(d)", doc)
    assert tab.evaluate("window.pressed === undefined")
