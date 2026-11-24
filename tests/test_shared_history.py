"""The record of what has been saved, shared through the folder you chose, in
a real Chromium with the extension's own files. Each copy of Statement Sweep
writes only its own file there, holding only what it saved into that folder,
and reads every other copy's. The folder is a stand-in in the browser's own
storage. Every id and date here is invented."""
import json
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


# A fresh page with `store` as chrome.storage.local, an empty chosen folder
# holding `files` (name: text) in its history folder, and the sync run with
# `options`. Answers the sync's result, the store after it, and every file in
# the history folder.
def sync(tab, store=None, files=None, options=None):
    tab.goto(BASE)
    tab.evaluate("s => sessionStorage.setItem('seed', JSON.stringify(s))", store or {})
    tab.goto(BASE)
    return tab.evaluate("""async ({ files, options }) => {
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
      const opts = {};
      // True for the first `wantedFor` asks, then false: a caller giving up part way.
      if (options.wantedFor !== undefined) { let left = options.wantedFor; opts.stillWanted = () => left-- > 0; }
      const result = await shared.syncShared(chosen, opts);
      const out = {};
      const dir = await chosen.getDirectoryHandle('Statement Sweep History').catch(() => null);
      if (dir) for await (const [n, h] of dir.entries()) out[n] = await (await h.getFile()).text();
      return { result, store: __store, files: out };
    }""", {"files": files or {}, "options": options or {}})


def other(**over):
    file = {"format": "statement-sweep-history", "version": 1, "exportedAt": "2026-11-20T10:00:00.000Z",
            "sites": {"amex": ["o1", "o2"]}, "dates": {"amex": {"o1": "2026-09-28", "o2": "2026-10-28"}}}
    file.update(over)
    return json.dumps(file)


SAVED_HERE = {"copyId": "mine", "sharedFolder": "t1", "seen:amex": ["a1", "old"], "shared:t1:amex": ["a1"], "shared:t0:amex": ["old"],
              "dates:amex": {"a1": "2026-10-28", "old": "2025-01-28"}, "tax:amex": ["a1"],
              "naming": {"base": "Secret Base", "folder": "{provider}", "file": "", "providers": {}}, "off": ["chase"]}


def test_nothing_saved_into_the_folder_writes_no_file(tab):
    got = sync(tab, {"copyId": "mine", "seen:amex": ["x1"]})
    assert got["result"]["wrote"] is False
    assert got["files"] == {}


def test_this_copy_writes_only_what_it_saved_into_the_folder(tab):
    got = sync(tab, SAVED_HERE)
    assert got["result"]["wrote"] is True
    assert list(got["files"]) == ["mine.json"]
    file = json.loads(got["files"]["mine.json"])
    # Only what went into this folder: "old" was saved into another one.
    assert file["sites"] == {"amex": ["a1"]}
    assert file["dates"] == {"amex": {"a1": "2026-10-28"}}
    assert file["tax"] == {"amex": ["a1"]}
    # Never the settings: folder and file names and switched-off sites stay here.
    assert "naming" not in file and "off" not in file


def test_the_written_file_reads_back_as_a_history_file(tab):
    got = sync(tab, SAVED_HERE)
    back = tab.evaluate("async (t) => (await import('/history.js')).readHistory(t)", got["files"]["mine.json"])
    assert back["ok"] is True and back["sites"] == {"amex": ["a1"]}


def test_another_copys_file_is_learned_and_left_as_it_was(tab):
    text = other()
    got = sync(tab, {"copyId": "mine", "seen:amex": ["a1"]}, {"theirs.json": text})
    assert got["result"]["imported"] == 2 and got["result"]["read"] == ["theirs.json"]
    assert got["store"]["seen:amex"] == ["a1", "o1", "o2"]
    assert got["store"]["dates:amex"] == {"o1": "2026-09-28", "o2": "2026-10-28"}
    # Learned from the folder, not saved into it by this copy.
    assert not [k for k in got["store"] if k.startswith("shared:")]
    assert got["files"]["theirs.json"] == text


def test_learning_never_changes_settings_here(tab):
    text = other(naming={"base": "Theirs", "folder": "", "file": "", "providers": {}}, off=["amex"])
    got = sync(tab, {"copyId": "mine", "naming": {"base": "Mine", "folder": "", "file": "", "providers": {}}}, {"theirs.json": text})
    assert got["store"]["naming"]["base"] == "Mine"
    assert "off" not in got["store"]


def test_this_copys_own_file_is_read_back_so_going_back_to_a_folder_keeps_it(tab):
    # Saved a2 into this folder before, then chose another folder and came back.
    mine = other(sites={"amex": ["a2"]}, dates={"amex": {"a2": "2026-09-28"}})
    store = {"copyId": "mine", "sharedFolder": "t2", "seen:amex": ["a1", "a2"], "shared:t2:amex": ["a1"]}
    got = sync(tab, store, {"mine.json": mine})
    assert got["result"]["read"] == [] and got["result"]["imported"] == 0
    assert got["store"]["shared:t2:amex"] == ["a1", "a2"]
    assert json.loads(got["files"]["mine.json"])["sites"] == {"amex": ["a1", "a2"]}


def test_a_file_of_this_copys_name_written_by_another_copy_gets_this_copy_a_new_name(tab):
    # A copied Chrome profile: the same id, and documents this copy never saved.
    theirs = other(sites={"amex": ["z1"]}, dates={})
    got = sync(tab, {"copyId": "mine", "sharedFolder": "t1", "seen:amex": ["a1"], "shared:t1:amex": ["a1"]}, {"mine.json": theirs})
    renamed = got["store"]["copyId"]
    assert renamed != "mine"
    assert got["files"]["mine.json"] == theirs
    assert got["store"]["seen:amex"] == ["a1", "z1"]
    assert json.loads(got["files"][f"{renamed}.json"])["sites"] == {"amex": ["a1"]}
    assert "another copy with this copy's id" in got["result"]["problems"][0]["problem"]


@pytest.mark.parametrize("text", ["{broken", other(version=2)])
def test_this_copys_own_file_that_cannot_be_used_is_left_alone_and_not_written(tab, text):
    got = sync(tab, {"copyId": "mine", "sharedFolder": "t1", "seen:amex": ["a1"], "shared:t1:amex": ["a1"]}, {"mine.json": text, "good.json": other()})
    assert got["files"]["mine.json"] == text and got["result"]["wrote"] is False
    assert got["result"]["problems"][0]["name"] == "mine.json"
    assert "left as it is" in got["result"]["problems"][0]["problem"]
    # The other copies' files are still learned.
    assert got["store"]["seen:amex"] == ["a1", "o1", "o2"]


@pytest.mark.parametrize("text, says", [
    ("{not json", "could not be read"),
    (other(version=2), "newer Statement Sweep"),
    (json.dumps({"hello": 1}), "not a Statement Sweep history file"),
])
def test_a_file_that_cannot_be_used_is_reported_left_alone_and_the_rest_still_learned(tab, text, says):
    got = sync(tab, {"copyId": "mine"}, {"bad.json": text, "good.json": other()})
    problems = got["result"]["problems"]
    assert [p["name"] for p in problems] == ["bad.json"] and says in problems[0]["problem"]
    assert got["store"]["seen:amex"] == ["o1", "o2"]
    assert got["files"]["bad.json"] == text


def test_a_site_this_copy_does_not_know_stays_in_its_file(tab):
    text = other(sites={"amex": ["o1"], "future-bank": ["f1"]}, dates={})
    got = sync(tab, {"copyId": "mine"}, {"theirs.json": text})
    assert got["result"]["unknown"] == ["future-bank"]
    assert got["files"]["theirs.json"] == text


def test_files_that_are_not_json_are_passed_over(tab):
    got = sync(tab, {"copyId": "mine"}, {".DS_Store": "x", "notes.txt": "x", "good.json": other()})
    assert got["result"]["read"] == ["good.json"] and got["result"]["problems"] == []


def test_with_sharing_turned_off_nothing_is_read_or_written(tab):
    got = sync(tab, {**SAVED_HERE, "historySharingOff": True}, {"theirs.json": other()})
    assert got["result"] == {"off": True}
    assert got["store"]["seen:amex"] == ["a1", "old"]
    assert list(got["files"]) == ["theirs.json"]


def test_a_sync_no_longer_wanted_writes_nothing(tab):
    got = sync(tab, SAVED_HERE, options={"wantedFor": 0}, files={"theirs.json": other()})
    assert got["result"] == {"gaveUp": True}
    assert list(got["files"]) == ["theirs.json"]
    assert got["store"]["seen:amex"] == ["a1", "old"]


def test_a_sync_given_up_part_way_changes_nothing_after(tab):
    two = other(sites={"amex": ["p1"]}, dates={})
    got = sync(tab, SAVED_HERE, options={"wantedFor": 1}, files={"a.json": other(), "b.json": two})
    assert got["result"] == {"gaveUp": True}
    # The first file was taken in before the sync was given up on; nothing after.
    assert got["store"]["seen:amex"] == ["a1", "old", "o1", "o2"]
    assert sorted(got["files"]) == ["a.json", "b.json"]


def test_a_record_too_large_to_read_back_is_not_written(tab):
    ids = [f"d{n}" for n in range(20001)]
    got = sync(tab, {"copyId": "mine", "sharedFolder": "t1", "seen:amex": ids, "shared:t1:amex": ids})
    assert got["result"]["wrote"] is False
    assert "too large" in got["result"]["problems"][0]["problem"]
    assert got["files"] == {}


def test_a_copy_without_an_id_is_given_one_that_is_kept(tab):
    got = sync(tab, {"sharedFolder": "t1", "seen:amex": ["a1"], "shared:t1:amex": ["a1"]})
    made = got["store"]["copyId"]
    assert len(made) >= 16 and list(got["files"]) == [f"{made}.json"]


def test_note_and_forget_what_was_saved_into_the_folder(tab):
    tab.goto(BASE)
    tab.evaluate("sessionStorage.setItem('seed', JSON.stringify({'seen:amex': ['a1'], 'shared:t0:chase': ['c0']}))")
    tab.goto(BASE)
    got = tab.evaluate("""async () => {
      const s = await import('/shared-history.js');
      const token = await s.folderToken();
      await s.noteShared('amex', ['a1'], token);
      await s.noteShared('amex', ['a1', 'a2'], token);
      const noted = { ...__store };
      await s.forgetShared();
      // A save that finishes after a new folder was chosen notes into the old token.
      await s.noteShared('amex', ['late'], token);
      return [token, noted, { ...__store }, await s.folderToken()];
    }""")
    token, noted, after, new_token = got
    assert noted[f"shared:{token}:amex"] == ["a1", "a2"] and noted["shared:t0:chase"] == ["c0"]
    assert new_token != token and after["seen:amex"] == ["a1"]
    assert [k for k in after if k.startswith("shared:")] == [f"shared:{token}:amex"]
    assert not [k for k in after if k.startswith(f"shared:{new_token}:")]


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


def test_choosing_a_folder_starts_afresh_on_what_is_known_to_be_in_it(stab):
    settings(stab, {"seen:amex": ["a1"], "sharedFolder": "t0", "shared:t0:amex": ["a1"]})
    choose(stab, "First")
    stab.wait_for_function("__store.sharedFolder !== 't0'")
    assert not [k for k in stab.evaluate("__store") if k.startswith("shared:")]
    assert stab.evaluate("__store['seen:amex']") == ["a1"]
    # Stored before the token changes, so a save that sees the new token saves into the new folder.
    assert stab.evaluate("__sent") == [{"type": "syncHistory"}]


def test_use_downloads_forgets_what_was_saved_into_the_folder(stab):
    settings(stab, {"sharedFolder": "t0", "shared:t0:amex": ["a1"]})
    choose(stab, "First")
    stab.evaluate("() => { __store['shared:' + __store.sharedFolder + ':amex'] = ['a1']; window.__was = __store.sharedFolder; }")
    stab.click("#useDownloads")
    stab.wait_for_function("__store.sharedFolder !== __was && !Object.keys(__store).some((k) => k.startsWith('shared:'))")


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
