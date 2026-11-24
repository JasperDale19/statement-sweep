"""The record of what has been saved, shared through the folder you chose, in
a real Chromium with the extension's own files. Each site that saves into the
folder writes a new entry there, never over another, holding only what it
saved there; every entry is read before a run. Whatever goes wrong, the worst
that may follow is a document saved twice, never one left unsaved. The folder
is a stand-in in the browser's own storage. Every id and date is invented."""
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


def entry(**over):
    file = {"format": "statement-sweep-history", "version": 1, "exportedAt": "2026-11-20T10:00:00.000Z",
            "sites": {"amex": ["o1", "o2"]}, "dates": {"amex": {"o1": "2026-09-28", "o2": "2026-10-28"}}}
    file.update(over)
    return json.dumps(file)


def write(site, ids, when="2026-11-24T14:05:09"):
    return f"async (s, root) => s.writeEntry(root, {json.dumps(site)}, {json.dumps(ids)}, new Date({json.dumps(when)}))"


STORE = {"seen:amex": ["a1", "a2", "old"], "dates:amex": {"a1": "2026-10-28", "a2": "2026-09-28", "old": "2025-01-28"},
         "tax:amex": ["a1"], "naming": {"base": "Secret Base", "folder": "{provider}", "file": "", "providers": {}}, "off": ["chase"]}
NAME = re.compile(r"^2026-11-24 140509 [0-9a-f-]{36}\.json$")


# -- reading ---------------------------------------------------------------------

def test_with_no_history_folder_nothing_is_read_and_nothing_is_made(tab):
    got = run(tab, READ, {"seen:amex": ["a1"]})
    assert got["result"] == {"read": [], "imported": 0, "unknown": [], "problems": []}
    assert got["files"] == {}


def test_every_entry_is_learned_and_left_as_it_was(tab):
    one, two = entry(), entry(sites={"amex": ["o2", "o3"]}, dates={})
    got = run(tab, READ, {"seen:amex": ["a1"]}, {"x.json": one, "y.json": two})
    assert got["result"]["read"] == ["x.json", "y.json"] and got["result"]["imported"] == 3
    assert got["store"]["seen:amex"] == ["a1", "o1", "o2", "o3"]
    assert got["store"]["dates:amex"] == {"o1": "2026-09-28", "o2": "2026-10-28"}
    assert got["files"] == {"x.json": one, "y.json": two}


def test_reading_again_learns_nothing_new(tab):
    got = run(tab, READ, {"seen:amex": ["o1", "o2"]}, {"x.json": entry()})
    assert got["result"]["imported"] == 0 and got["store"]["seen:amex"] == ["o1", "o2"]


def test_learning_never_changes_settings_here(tab):
    text = entry(naming={"base": "Theirs", "folder": "", "file": "", "providers": {}}, off=["amex"])
    got = run(tab, READ, {"naming": {"base": "Mine", "folder": "", "file": "", "providers": {}}}, {"x.json": text})
    assert got["store"]["naming"]["base"] == "Mine" and "off" not in got["store"]


@pytest.mark.parametrize("text, says", [
    ("{not json", "could not be read"),
    (entry(version=2), "newer Statement Sweep"),
    (json.dumps({"hello": 1}), "not a Statement Sweep history file"),
])
def test_an_entry_that_cannot_be_used_is_reported_left_alone_and_the_rest_still_learned(tab, text, says):
    got = run(tab, READ, {}, {"bad.json": text, "good.json": entry()})
    problems = got["result"]["problems"]
    assert [p["name"] for p in problems] == ["bad.json"] and says in problems[0]["problem"]
    assert got["store"]["seen:amex"] == ["o1", "o2"]
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
    assert got["result"] == {"off": True} and "seen:amex" not in got["store"]


# -- writing -----------------------------------------------------------------------

def test_an_entry_holds_only_the_documents_given_with_their_dates_and_tax_marks(tab):
    got = run(tab, write("amex", ["a1", "a2"]), STORE)
    assert NAME.match(got["result"]), got["result"]
    file = json.loads(got["files"][got["result"]])
    assert file["sites"] == {"amex": ["a1", "a2"]}
    assert file["dates"] == {"amex": {"a1": "2026-10-28", "a2": "2026-09-28"}}
    assert file["tax"] == {"amex": ["a1"]}
    # Never the settings: folder and file names and switched-off sites stay here.
    assert "naming" not in file and "off" not in file


def test_an_entry_reads_back_as_a_history_file(tab):
    got = run(tab, write("amex", ["a1"]), STORE)
    back = tab.evaluate("async (t) => (await import('/history.js')).readHistory(t)", got["files"][got["result"]])
    assert back["ok"] is True and back["sites"] == {"amex": ["a1"]}


def test_entries_written_at_the_same_moment_never_write_over_each_other(tab):
    call = """async (s, root) => {
      const when = new Date('2026-11-24T14:05:09');
      return Promise.all([s.writeEntry(root, 'amex', ['a1'], when), s.writeEntry(root, 'amex', ['a2'], when)]);
    }"""
    existing = {"2026-11-24 140509 old.json": entry()}
    got = run(tab, call, STORE, existing)
    assert len(set(got["result"])) == 2 and len(got["files"]) == 3
    assert got["files"]["2026-11-24 140509 old.json"] == entry()


def test_nothing_to_write_writes_nothing(tab):
    got = run(tab, write("amex", []), STORE)
    assert got["result"] is None and got["files"] == {}


def test_with_sharing_turned_off_no_entry_is_written(tab):
    got = run(tab, write("amex", ["a1"]), {**STORE, "historySharingOff": True})
    assert got["result"] is None and got["files"] == {}


def test_more_documents_than_an_entry_can_hold_is_refused(tab):
    ids = [f"d{n}" for n in range(20001)]
    call = f"async (s, root) => s.writeEntry(root, 'amex', {json.dumps(ids)}).then(() => 'written', (e) => e.message)"
    got = run(tab, call, {})
    assert "more than one history file can hold" in got["result"] and got["files"] == {}


def test_an_entry_written_here_is_learned_by_another_copy(tab):
    mine = run(tab, write("amex", ["a1", "a2"]), STORE)
    got = run(tab, READ, {"seen:amex": ["z9"]}, mine["files"])
    assert got["store"]["seen:amex"] == ["z9", "a1", "a2"]
    assert got["store"]["tax:amex"] == ["a1"]


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
    settings(stab, {"seen:amex": ["a1"]}, {"read": ["theirs.json"], "imported": 3, "problems": [], "wrote": False})
    choose(stab, "Shared")
    stab.wait_for_function("document.getElementById('historyStatus').textContent !== ''")
    assert stab.evaluate("__sent") == [{"type": "syncHistory"}]
    assert stab.inner_text("#historyStatus") == "Learned 3 documents saved into this folder by another copy of Statement Sweep."


def test_a_problem_with_a_history_file_is_shown(stab):
    settings(stab, {}, {"read": [], "imported": 0, "problems": [{"name": "bad.json", "problem": "This is not a Statement Sweep history file."}], "wrote": False})
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
