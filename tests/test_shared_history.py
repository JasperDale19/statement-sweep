"""The record of what has been saved, shared through the folder you chose, in
a real Chromium with the extension's own files. Each site that saves into the
folder writes a new entry there, never over another, holding only what it
saved there; every entry is read before a run. Whatever goes wrong, the worst
that may follow is a document saved twice, never one left unsaved. The folder
is a stand-in in the browser's own storage. Every id and date is invented."""
import hashlib
import json
import re
import pytest
from playwright.sync_api import sync_playwright
from test_history import BASE, CHROME, ROOT, TYPES

DIR = "Statement Sweep History"


def serve(route):
    path = ROOT / route.request.url[len(BASE):].split("?")[0]
    if path.is_file():
        route.fulfill(status=200, content_type=TYPES.get(path.suffix, "text/plain"), body=path.read_text())
    else:
        route.fulfill(status=200, content_type="text/html", body="<!doctype html><title>x</title>")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def page_of(browser, *scripts):
    t = browser.new_page()
    for script in scripts:
        t.add_init_script(script)
    t.route(BASE + "**", serve)
    return t


@pytest.fixture(scope="module")
def tab(browser):
    t = page_of(browser, CHROME)
    yield t
    t.context.close()


# A fresh page with `store` as chrome.storage.local and an empty chosen folder
# holding `files` (name: text) in its history folder; then `call` (an async
# function of the module and the folder) is run. Answers what it answered, the
# store after it, and every file in the history folder.
def run(tab, call, store=None, files=None):
    tab.goto(BASE)
    tab.evaluate("s => sessionStorage.setItem('seed', JSON.stringify(s))", store or {})
    tab.goto(BASE)
    return tab.evaluate("""async ({ call, files }) => {
      const root = await navigator.storage.getDirectory();
      // Nothing checked in an earlier test's folder of the same name carries over.
      await (await import('/folders.js')).forgetFolders();
      await root.removeEntry('Chosen', { recursive: true }).catch(() => {});
      const chosen = await root.getDirectoryHandle('Chosen', { create: true });
      if (Object.keys(files).length) {
        const dir = await chosen.getDirectoryHandle('Statement Sweep History', { create: true });
        for (const [name, text] of Object.entries(files)) {
          const w = await (await dir.getFileHandle(name, { create: true })).createWritable();
          await w.write(text); await w.close();
        }
      }
      const shared = await import('/shared-history.js');
      const result = await (0, eval)(call)(shared, chosen);
      const out = {};
      const dir = await chosen.getDirectoryHandle('Statement Sweep History').catch(() => null);
      if (dir) for await (const [n, h] of dir.entries()) out[n] = await (await h.getFile()).text();
      return { result, store: __store, files: out };
    }""", {"call": call, "files": files or {}})


READ = "async (s, root) => s.readShared(root)"
PDF_A = "%PDF-1.4 invented a\n%%EOF"
PDF_B = "%PDF-1.4 invented b, longer\n%%EOF"
PATH_A = "Statement Sweep/American Express/2026-10-28 American Express Statement.pdf"


def facts(path, text):
    return {"path": path, "size": len(text.encode()), "sha256": hashlib.sha256(text.encode()).hexdigest()}


def entry(files=None, **over):
    file = {"format": "statement-sweep-history", "version": 1, "exportedAt": "2026-11-20T10:00:00.000Z",
            "sites": {"amex": ["o1"]}, "dates": {"amex": {"o1": "2026-10-28"}},
            "files": files if files is not None else {"amex": {"o1": facts(PATH_A, PDF_A)}}}
    file.update(over)
    return json.dumps(file)


# Runs `call` with `pdfs` (path: text) put in the chosen folder first.
def with_pdfs(pdfs, call):
    return f"""async (s, root) => {{
      for (const [path, text] of Object.entries({json.dumps(pdfs)})) {{
        const parts = path.split('/'); const name = parts.pop(); let dir = root;
        for (const part of parts) dir = await dir.getDirectoryHandle(part, {{ create: true }});
        const w = await (await dir.getFileHandle(name, {{ create: true }})).createWritable(); await w.write(text); await w.close();
      }}
      return ({call})(s, root);
    }}"""


CHECK = """async (s, root) => s.inFolderAlready(root, 'amex', ['o1', 'o2', 'mine'], (await s.readShared(root)).index).then((set) => [...set])"""
STORE = {"seen:amex": ["a1", "a2"], "dates:amex": {"a1": "2026-10-28", "a2": "2026-09-28"}, "tax:amex": ["a1"],
         "naming": {"base": "Secret Base", "folder": "{provider}", "file": "", "providers": {}}, "off": ["chase"]}
NAME = re.compile(r"^2026-11-24 140509 [0-9a-f-]{36}\.json$")


# -- reading ---------------------------------------------------------------------

def test_with_no_history_folder_nothing_is_listed_and_nothing_is_made(tab):
    got = run(tab, READ, {"seen:amex": ["a1"]})
    assert got["result"] == {"index": {}, "read": [], "listed": 0, "unknown": [], "problems": []}
    assert got["files"] == {}


def test_entries_are_listed_and_nothing_is_added_to_this_copys_record(tab):
    two = entry({"amex": {"o1": facts("elsewhere/o1.pdf", PDF_A), "o2": facts("x/o2.pdf", PDF_B)}}, sites={"amex": ["o1", "o2"]})
    got = run(tab, READ, {"seen:amex": ["a1"]}, {"x.json": entry(), "y.json": two})
    assert got["result"]["read"] == ["x.json", "y.json"] and got["result"]["listed"] == 2
    assert got["result"]["index"]["amex"]["o1"] == [facts(PATH_A, PDF_A), facts("elsewhere/o1.pdf", PDF_A)]
    # Claims only: this copy's own record is left as it was.
    assert got["store"] == {"seen:amex": ["a1"]}
    assert got["files"] == {"x.json": entry(), "y.json": two}


def test_an_entry_without_saved_files_lists_nothing(tab):
    got = run(tab, READ, {}, {"x.json": entry(files={})})
    assert got["result"]["read"] == ["x.json"] and got["result"]["listed"] == 0


@pytest.mark.parametrize("text, says", [
    ("{not json", "could not be read"),
    (entry(version=2), "newer Statement Sweep"),
    (json.dumps({"hello": 1}), "not a Statement Sweep history file"),
    (entry({"amex": {"o1": {**facts(PATH_A, PDF_A), "path": "../outside.pdf"}}}), "not a saved file"),
    (entry({"amex": {"o1": {**facts(PATH_A, PDF_A), "sha256": "nope"}}}), "not a saved file"),
])
def test_an_entry_that_cannot_be_used_is_reported_left_alone_and_the_rest_still_read(tab, text, says):
    got = run(tab, READ, {}, {"bad.json": text, "good.json": entry()})
    problems = got["result"]["problems"]
    assert [p["name"] for p in problems] == ["bad.json"] and says in problems[0]["problem"]
    assert got["result"]["read"] == ["good.json"]
    assert got["files"]["bad.json"] == text


def test_a_site_this_copy_does_not_know_stays_in_its_entry(tab):
    text = entry(sites={"amex": ["o1"], "future-bank": ["f1"]}, dates={})
    got = run(tab, READ, {}, {"x.json": text})
    assert got["result"]["unknown"] == ["future-bank"] and got["files"]["x.json"] == text


def test_files_that_are_not_entries_are_passed_over(tab):
    got = run(tab, READ, {}, {".DS_Store": "x", "notes.txt": "x", "._x.json": "x", "good.json": entry()})
    assert got["result"]["read"] == ["good.json"] and got["result"]["problems"] == []


def test_with_sharing_turned_off_nothing_is_read(tab):
    got = run(tab, READ, {"historySharingOff": True}, {"x.json": entry()})
    assert got["result"] == {"off": True}


# -- checking a claim in the folder ------------------------------------------------

def test_a_document_whose_file_is_in_the_folder_as_claimed_counts_as_there(tab):
    got = run(tab, with_pdfs({PATH_A: PDF_A}, CHECK), {}, {"x.json": entry()})
    assert got["result"] == ["o1"]
    assert "seen:amex" not in got["store"]


@pytest.mark.parametrize("pdfs, why", [
    ({}, "the file has not synced down yet, or never uploaded"),
    ({PATH_A: PDF_B}, "a different file at that path"),
    ({PATH_A: PDF_A[:-3] + "xyz"}, "the same size but other bytes"),
    ({"Statement Sweep/elsewhere.pdf": PDF_A}, "the right file at another path"),
])
def test_a_claim_whose_file_is_not_there_as_claimed_does_not_count(tab, pdfs, why):
    got = run(tab, with_pdfs(pdfs, CHECK), {}, {"x.json": entry()})
    assert got["result"] == [], why


def test_a_file_that_checked_out_is_not_read_again_in_the_same_folder(tab):
    call = with_pdfs({PATH_A: PDF_A}, """async (s, root) => {
      const index = (await s.readShared(root)).index;
      const first = [...await s.inFolderAlready(root, 'amex', ['o1'], index)];
      // Gone from the folder now, and still counted: it was there, as a document this copy saved would be.
      await (await (await (await root.getDirectoryHandle('Statement Sweep')).getDirectoryHandle('American Express'))).removeEntry('2026-10-28 American Express Statement.pdf');
      const again = [...await s.inFolderAlready(root, 'amex', ['o1'], index)];
      return [first, again];
    }""")
    got = run(tab, call, {}, {"x.json": entry()})
    assert got["result"] == [["o1"], ["o1"]]


def test_a_file_that_checked_out_in_one_folder_is_checked_again_in_another(tab):
    call = with_pdfs({PATH_A: PDF_A}, """async (s, root) => {
      const index = (await s.readShared(root)).index;
      const there = [...await s.inFolderAlready(root, 'amex', ['o1'], index)];
      const other = await (await navigator.storage.getDirectory()).getDirectoryHandle('Empty B', { create: true });
      const inB = [...await s.inFolderAlready(other, 'amex', ['o1'], index)];
      return [there, inB];
    }""")
    got = run(tab, call, {}, {"x.json": entry()})
    assert got["result"] == [["o1"], []]


# -- writing -----------------------------------------------------------------------

def write(saved, when="2026-11-24T14:05:09"):
    return f"async (s, root) => s.writeEntry(root, 'amex', {json.dumps(saved)}, new Date({json.dumps(when)}))"


def test_an_entry_holds_only_the_documents_given_with_their_files_dates_and_tax_marks(tab):
    saved = [{"id": "a1", "facts": facts(PATH_A, PDF_A)}, {"id": "a2", "facts": facts("p/a2.pdf", PDF_B)}]
    got = run(tab, write(saved), STORE)
    assert NAME.match(got["result"]), got["result"]
    file = json.loads(got["files"][got["result"]])
    assert file["sites"] == {"amex": ["a1", "a2"]}
    assert file["files"] == {"amex": {"a1": facts(PATH_A, PDF_A), "a2": facts("p/a2.pdf", PDF_B)}}
    assert file["dates"] == {"amex": {"a1": "2026-10-28", "a2": "2026-09-28"}}
    assert file["tax"] == {"amex": ["a1"]}
    # Never the settings: folder and file names and switched-off sites stay here.
    assert "naming" not in file and "off" not in file


def test_an_entry_reads_back_as_a_history_file(tab):
    got = run(tab, write([{"id": "a1", "facts": facts(PATH_A, PDF_A)}]), STORE)
    back = tab.evaluate("async (t) => (await import('/history.js')).readHistory(t)", got["files"][got["result"]])
    assert back["ok"] is True and back["sites"] == {"amex": ["a1"]} and back["files"] == {"amex": {"a1": facts(PATH_A, PDF_A)}}


def test_facts_of_a_saved_file_are_its_path_and_the_size_and_sha256_of_its_bytes(tab):
    # Taken from the bytes saved, not from whatever is at the path now.
    call = with_pdfs({PATH_A: PDF_B}, f"async (s, root) => s.factsOf({json.dumps(PATH_A)}, new Blob([{json.dumps(PDF_A)}]))")
    got = run(tab, call)
    assert got["result"] == facts(PATH_A, PDF_A)


def test_entries_written_at_the_same_moment_never_write_over_each_other(tab):
    call = f"""async (s, root) => {{
      const when = new Date('2026-11-24T14:05:09');
      const one = {json.dumps([{"id": "a1", "facts": facts(PATH_A, PDF_A)}])};
      return Promise.all([s.writeEntry(root, 'amex', one, when), s.writeEntry(root, 'amex', one, when)]);
    }}"""
    got = run(tab, call, STORE, {"2026-11-24 140509 old.json": entry()})
    assert len(set(got["result"])) == 2 and len(got["files"]) == 3
    assert got["files"]["2026-11-24 140509 old.json"] == entry()


def test_nothing_to_write_writes_nothing(tab):
    got = run(tab, write([]), STORE)
    assert got["result"] is None and got["files"] == {}


def test_with_sharing_turned_off_no_entry_is_written(tab):
    got = run(tab, write([{"id": "a1", "facts": facts(PATH_A, PDF_A)}]), {**STORE, "historySharingOff": True})
    assert got["result"] is None and got["files"] == {}


def test_more_documents_than_an_entry_can_hold_is_refused(tab):
    saved = [{"id": f"d{n}", "facts": facts(f"p/{n}.pdf", "x")} for n in range(20001)]
    call = f"async (s, root) => s.writeEntry(root, 'amex', {json.dumps(saved)}).then(() => 'written', (e) => e.message)"
    got = run(tab, call, {})
    assert "more than one history file can hold" in got["result"] and got["files"] == {}


def test_an_entry_written_here_is_found_by_another_copy_using_the_same_folder(tab):
    mine = run(tab, with_pdfs({PATH_A: PDF_A}, f"""async (s, root) => s.writeEntry(root, 'amex', [{{ id: 'o1', facts: await s.factsOf({json.dumps(PATH_A)}, new Blob([{json.dumps(PDF_A)}])) }}])"""), STORE)
    got = run(tab, with_pdfs({PATH_A: PDF_A}, CHECK), {"seen:amex": ["z9"]}, mine["files"])
    assert got["result"] == ["o1"] and got["store"]["seen:amex"] == ["z9"]


# -- Settings ----------------------------------------------------------------------

RUNTIME = """
chrome.runtime = { sendMessage: async (m) => { (window.__sent = window.__sent || []).push(m); return window.__answer; } };
"""


@pytest.fixture(scope="module")
def stab(browser):
    t = page_of(browser, CHROME, RUNTIME)
    yield t
    t.context.close()


# Settings with `store`, no folder chosen yet, and the engine's answer to a
# sync. `picks` are the folders the picker answers, in turn.
def settings(stab, store, answer=None):
    stab.goto(BASE)
    stab.evaluate("async () => (await import('/folders.js')).forgetFolders()")
    stab.evaluate("s => sessionStorage.setItem('seed', JSON.stringify(s))", store)
    stab.goto(BASE + "options.html")
    stab.wait_for_selector("#scope option[value=amex]", state="attached")
    stab.wait_for_function("document.getElementById('historyCount').textContent !== ''")
    stab.evaluate("""(answer) => {
      window.__answer = answer;
      window.showDirectoryPicker = async () => (await navigator.storage.getDirectory()).getDirectoryHandle(__picks.shift(), { create: true });
    }""", answer)


def choose(stab, name):
    stab.evaluate("(n) => { window.__picks = [n]; }", name)
    stab.click("#chooseDest")
    stab.wait_for_function("(n) => document.getElementById('destName').textContent === n", arg=name)


def test_choosing_a_folder_asks_for_a_sync_and_says_what_it_learned(stab):
    settings(stab, {"seen:amex": ["a1"]}, {"read": ["theirs.json"], "listed": 3, "problems": []})
    choose(stab, "Shared")
    stab.wait_for_function("document.getElementById('historyStatus').textContent !== ''")
    assert stab.evaluate("__sent") == [{"type": "syncHistory"}]
    assert stab.inner_text("#historyStatus") == "Found 3 documents saved into this folder by another copy of Statement Sweep. Each is checked in the folder before it is skipped."


def test_a_problem_with_a_history_file_is_shown(stab):
    settings(stab, {}, {"read": [], "listed": 0, "problems": [{"name": "bad.json", "problem": "This is not a Statement Sweep history file."}]})
    choose(stab, "Shared")
    stab.wait_for_function("document.getElementById('historyProblem').textContent !== ''")
    assert stab.inner_text("#historyProblem") == "bad.json in Statement Sweep History was not used: This is not a Statement Sweep history file."


def test_sharing_is_on_with_a_folder_and_can_be_turned_off(stab):
    settings(stab, {})
    assert stab.locator("#shareRow").is_hidden()
    choose(stab, "Shared")
    assert stab.locator("#shareRow").is_visible() and stab.is_checked("#shareHistory")
    stab.uncheck("#shareHistory")
    stab.wait_for_function("__store.historySharingOff === true")
    stab.check("#shareHistory")
    stab.wait_for_function("__store.historySharingOff === false")


def test_an_engine_that_does_not_answer_leaves_the_page_quiet(stab):
    settings(stab, {}, None)
    stab.evaluate("() => { chrome.runtime.sendMessage = async () => { throw new Error('Could not establish connection.'); }; }")
    choose(stab, "Shared")
    stab.wait_for_timeout(200)
    assert stab.inner_text("#historyProblem") == "" and stab.inner_text("#locationProblem") == ""


# -- saving two documents at once under one name -------------------------------------

def test_two_documents_saved_at_once_under_one_name_both_survive(tab):
    # Different PDFs of the same size, saved at the same moment to the same path.
    call = f"""async (s, root) => {{
      const f = await import('/folders.js');
      const path = {json.dumps(PATH_A)};
      const names = await Promise.all([f.writeFile(root, path, new Blob([{json.dumps(PDF_A)}])), f.writeFile(root, path, new Blob([{json.dumps(PDF_A[:-3] + "xyz")}]))]);
      const read = async (p) => {{ const parts = p.split('/'); const n = parts.pop(); let d = root; for (const x of parts) d = await d.getDirectoryHandle(x); return (await (await d.getFileHandle(n)).getFile()).text(); }};
      return {{ names, texts: await Promise.all(names.map(read)) }};
    }}"""
    got = run(tab, call)
    assert len(set(got["result"]["names"])) == 2, got["result"]["names"]
    assert sorted(got["result"]["texts"]) == sorted([PDF_A, PDF_A[:-3] + "xyz"])


def test_a_folder_that_never_answers_does_not_hold_up_later_saves(tab):
    call = f"""async (s, root) => {{
      const f = await import('/folders.js');
      // Thirty seconds become 50 ms here.
      const real = window.setTimeout;
      window.setTimeout = (fn, ms, ...rest) => real(fn, ms === 30000 ? 50 : ms, ...rest);
      const stuck = {{ getFileHandle: () => new Promise(() => {{}}), getDirectoryHandle: () => new Promise(() => {{}}) }};
      const first = f.writeFile(stuck, 'x.pdf', new Blob(['%PDF-'])).then(() => 'saved', (e) => e.message);
      const second = await f.writeFile(root, {json.dumps(PATH_A)}, new Blob([{json.dumps(PDF_A)}]));
      window.setTimeout = real;
      return [await first, second];
    }}"""
    got = run(tab, call)
    assert got["result"] == ["The chosen folder did not answer within 30 seconds.", PATH_A]
