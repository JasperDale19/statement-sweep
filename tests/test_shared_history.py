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


PDF_A = "%PDF-1.4 invented a\n%%EOF"
PDF_B = "%PDF-1.4 invented b, longer\n%%EOF"
PDF_C = PDF_A[:-3] + "xyz"
PATH_A = "Statement Sweep/American Express/2026-10-28 American Express Statement.pdf"
PATH_B = "Statement Sweep/American Express/2026-09-28 American Express Statement.pdf"


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


# What a call answered, or which error it threw and what it said.
SAFE = "(work) => work.then((v) => v, (e) => ({ threw: e instanceof Error ? e.constructor.name : 'other', says: String(e && e.message) }))"
RECONCILE = f"async (s, root) => ({SAFE})(s.reconcile(root))"
STORE = {"seen:amex": ["a1", "a2"], "dates:amex": {"a1": "2026-10-28", "a2": "2026-09-28"}, "tax:amex": ["a1"],
         "naming": {"base": "Secret Base", "folder": "{provider}", "file": "", "providers": {}}, "off": ["chase"]}
NAME = re.compile(r"^2026-11-24 140509 [0-9a-f-]{36}\.json$")
# The file at PATH_A, rewritten with `text` a moment later, so its time of last change moves.
REWRITE = """async (root, text) => {
  await new Promise((r) => setTimeout(r, 20));
  const dir = await (await root.getDirectoryHandle('Statement Sweep')).getDirectoryHandle('American Express');
  const w = await (await dir.getFileHandle('2026-10-28 American Express Statement.pdf')).createWritable(); await w.write(text); await w.close();
}"""


# -- reading the folder ------------------------------------------------------------

def test_a_folder_with_no_history_holds_nothing_and_nothing_is_made(tab):
    got = run(tab, RECONCILE, {"seen:amex": ["a1"]})
    assert got["result"] == {"record": {}, "files": {}, "documents": 0, "entries": 0, "hashed": 0, "unknown": [], "problems": []}
    assert got["files"] == {} and got["store"] == {"seen:amex": ["a1"]}


def test_a_document_whose_file_is_there_as_claimed_is_in_the_folders_record(tab):
    text = entry({"amex": {"o1": facts(PATH_A, PDF_A), "o2": facts(PATH_B, PDF_B)}}, sites={"amex": ["o1", "o2"]},
                 dates={"amex": {"o1": "2026-10-28", "o2": "2026-09-28"}}, tax={"amex": ["o2"]})
    got = run(tab, with_pdfs({PATH_A: PDF_A, PATH_B: PDF_B}, RECONCILE), {"seen:amex": ["a1"]}, {"x.json": text})
    r = got["result"]
    assert r["record"] == {"amex": {"ids": ["o1", "o2"], "dates": {"o1": "2026-10-28", "o2": "2026-09-28"}, "tax": ["o2"],
                                    "paths": {"o1": PATH_A, "o2": PATH_B},
                                    "sums": {"o1": facts(PATH_A, PDF_A)["sha256"], "o2": facts(PATH_B, PDF_B)["sha256"]}, "checked": ""}}
    assert r["documents"] == 2 and r["entries"] == 1 and r["hashed"] == 2
    assert {k: v["sha256"] for k, v in r["files"].items()} == {PATH_A: facts(PATH_A, PDF_A)["sha256"], PATH_B: facts(PATH_B, PDF_B)["sha256"]}
    # Reading changes nothing: not this copy's record, and not the entry.
    assert got["store"] == {"seen:amex": ["a1"]} and got["files"] == {"x.json": text}


def test_a_file_hashed_before_and_unchanged_is_not_read_again(tab):
    call = with_pdfs({PATH_A: PDF_A}, """async (s, root) => {
      const first = await s.reconcile(root);
      const again = await s.reconcile(root, { known: first.files });
      return [first.hashed, again.hashed, again.record.amex.ids, again.files];
    }""")
    first, again, ids, files = run(tab, call, {}, {"x.json": entry()})["result"]
    assert (first, again, ids) == (1, 0, ["o1"]) and list(files) == [PATH_A]


@pytest.mark.parametrize("pdfs, why", [
    ({}, "the file has not synced down yet, or never uploaded"),
    ({PATH_A: PDF_B}, "a different file at that path"),
    ({PATH_A: PDF_C}, "the same size but other bytes"),
    ({"Statement Sweep/elsewhere.pdf": PDF_A}, "the right file at another path"),
])
def test_a_claim_whose_file_is_not_there_as_claimed_is_not_in_the_record(tab, pdfs, why):
    got = run(tab, with_pdfs(pdfs, RECONCILE), {}, {"x.json": entry()})
    assert got["result"]["record"] == {} and got["result"]["documents"] == 0, why


def test_a_file_changed_since_it_was_hashed_is_read_again_and_no_longer_counts(tab):
    call = with_pdfs({PATH_A: PDF_A}, f"""async (s, root) => {{
      const first = await s.reconcile(root);
      await ({REWRITE})(root, {json.dumps(PDF_C)});
      const again = await s.reconcile(root, {{ known: first.files }});
      return [first.documents, again.documents, again.hashed];
    }}""")
    assert run(tab, call, {}, {"x.json": entry()})["result"] == [1, 0, 1]


def test_any_entry_that_names_where_the_file_is_will_do(tab):
    one = entry({"amex": {"o1": facts("gone/o1.pdf", PDF_A)}})
    two = entry({"amex": {"o1": facts(PATH_A, PDF_A)}})
    got = run(tab, with_pdfs({PATH_A: PDF_A}, RECONCILE), {}, {"a.json": one, "b.json": two})
    assert got["result"]["record"]["amex"]["paths"] == {"o1": PATH_A}


def test_the_latest_day_a_site_was_checked_is_in_the_record(tab):
    checked = lambda day: json.dumps({"format": "statement-sweep-history", "version": 1, "sites": {}, "checked": {"chase": day}})
    got = run(tab, RECONCILE, {}, {"a.json": checked("2026-11-20"), "b.json": checked("2026-11-22"), "c.json": checked("2026-11-21")})
    assert got["result"]["record"] == {"chase": {"ids": [], "dates": {}, "tax": [], "paths": {}, "sums": {}, "checked": "2026-11-22"}}
    assert got["result"]["entries"] == 3


@pytest.mark.parametrize("text, says", [
    ("{not json", "could not be read"),
    (json.dumps({"hello": 1}), "not a Statement Sweep history file"),
    (entry({"amex": {"o1": {**facts(PATH_A, PDF_A), "path": "../outside.pdf"}}}), "not a saved file"),
    (entry({"amex": {"o1": {**facts(PATH_A, PDF_A), "sha256": "nope"}}}), "not a saved file"),
    (entry(checked={"amex": "soon"}), "not a date"),
])
def test_an_entry_whose_words_are_wrong_is_reported_left_alone_and_the_rest_still_read(tab, text, says):
    got = run(tab, with_pdfs({PATH_A: PDF_A}, RECONCILE), {}, {"bad.json": text, "good.json": entry()})
    problems = got["result"]["problems"]
    assert [p["name"] for p in problems] == ["bad.json"] and says in problems[0]["problem"]
    assert got["result"]["record"]["amex"]["ids"] == ["o1"]
    assert got["files"]["bad.json"] == text


def test_an_entry_from_a_newer_statement_sweep_stops_the_reading(tab):
    # What it lists is not known here, and must not be taken for nothing.
    got = run(tab, with_pdfs({PATH_A: PDF_A}, RECONCILE), {}, {"new.json": entry(version=2), "good.json": entry()})
    assert got["result"]["threw"] == "FolderUnavailable"
    assert "newer Statement Sweep" in got["result"]["says"] and "Nothing was saved" in got["result"]["says"]


# A folder whose history, or one file, does what `fault` says.
def faulty(fault):
    return f"""async (s, root) => {{
      const fault = {json.dumps(fault)};
      const never = () => new Promise(() => {{}});
      const broken = () => Promise.reject(new DOMException('no', 'NotReadableError'));
      const wrap = (dir) => ({{
        kind: 'directory', name: dir.name,
        getDirectoryHandle: async (n, o) => wrap(await dir.getDirectoryHandle(n, o)),
        getFileHandle: async (n, o) => {{
          const h = await dir.getFileHandle(n, o);
          if (fault.file && n.endsWith('.pdf')) return {{ kind: 'file', name: n, getFile: fault.file === 'hang' ? never : broken }};
          return h;
        }},
        entries: async function* () {{
          for await (const [n, h] of dir.entries()) {{
            if (fault.entry && n.endsWith('.json')) yield [n, {{ kind: 'file', name: n, getFile: fault.entry === 'hang' ? never : broken }}];
            else yield [n, h];
          }}
        }},
      }});
      return ({SAFE})(s.reconcile(wrap(root), {{ stallMs: 80 }}));
    }}"""


@pytest.mark.parametrize("fault", [{"entry": "broken"}, {"entry": "hang"}, {"file": "broken"}, {"file": "hang"}])
def test_a_folder_that_cannot_be_read_is_never_taken_for_an_empty_one(tab, fault):
    got = run(tab, with_pdfs({PATH_A: PDF_A}, faulty(fault)), {}, {"x.json": entry()})
    assert got["result"]["threw"] == "FolderUnavailable", got["result"]
    assert "Nothing was saved" in got["result"]["says"]


def test_stop_ends_the_reading(tab):
    call = with_pdfs({PATH_A: PDF_A}, f"async (s, root) => ({SAFE})(s.reconcile(root, {{ stopped: () => true }}))")
    assert run(tab, call, {}, {"x.json": entry()})["result"]["threw"] == "Stopped"


def test_progress_counts_every_document_claimed(tab):
    text = entry({"amex": {"o1": facts(PATH_A, PDF_A), "o2": facts(PATH_B, PDF_B), "o3": facts("gone.pdf", PDF_A)}}, sites={"amex": ["o1", "o2", "o3"]})
    call = with_pdfs({PATH_A: PDF_A, PATH_B: PDF_B}, """async (s, root) => {
      const seen = [];
      await s.reconcile(root, { progress: (done, total) => seen.push([done, total]) });
      return seen;
    }""")
    seen = run(tab, call, {}, {"x.json": text})["result"]
    assert sorted(seen) == [[1, 3], [2, 3], [3, 3]]


def test_a_site_this_copy_does_not_know_stays_in_its_entry(tab):
    text = entry(sites={"amex": ["o1"], "future-bank": ["f1"]}, dates={})
    got = run(tab, RECONCILE, {}, {"x.json": text})
    assert got["result"]["unknown"] == ["future-bank"] and got["files"]["x.json"] == text


def test_files_that_are_not_entries_are_passed_over(tab):
    got = run(tab, with_pdfs({PATH_A: PDF_A}, RECONCILE), {}, {".DS_Store": "x", "notes.txt": "x", "._x.json": "x", "good.json": entry()})
    assert got["result"]["entries"] == 1 and got["result"]["problems"] == []


# -- looking once more before a document is skipped ---------------------------------

def still(change):
    return with_pdfs({PATH_A: PDF_A}, f"""async (s, root) => {{
      const folder = await s.reconcile(root);
      await ({change})(root);
      return ({SAFE})(s.stillThere(root, folder, 'amex', ['o1', 'never-listed']).then((set) => [...set]));
    }}""")


def test_a_document_still_in_the_folder_is_still_there(tab):
    assert run(tab, still("async () => {}"), {}, {"x.json": entry()})["result"] == ["o1"]


def test_a_document_removed_since_the_folder_was_read_is_no_longer_there(tab):
    remove = "async (root) => (await (await root.getDirectoryHandle('Statement Sweep')).getDirectoryHandle('American Express')).removeEntry('2026-10-28 American Express Statement.pdf')"
    assert run(tab, still(remove), {}, {"x.json": entry()})["result"] == []


def test_a_document_changed_since_the_folder_was_read_is_no_longer_there(tab):
    change = f"async (root) => ({REWRITE})(root, {json.dumps(PDF_C)})"
    assert run(tab, still(change), {}, {"x.json": entry()})["result"] == []


def test_two_documents_claimed_at_one_path_are_never_both_taken_for_its_file(tab):
    # Two entries name one path for different documents; the file holds the first's bytes.
    one = entry({"amex": {"o1": facts(PATH_A, PDF_A)}})
    two = entry({"amex": {"o2": facts(PATH_A, PDF_C)}}, sites={"amex": ["o2"]}, dates={})
    call = with_pdfs({PATH_A: PDF_A}, f"""async (s, root) => {{
      const folder = await s.reconcile(root);
      // As if both had settled, each against a different version of the file.
      folder.record.amex.paths.o2 = {json.dumps(PATH_A)};
      folder.record.amex.sums.o2 = {json.dumps(facts(PATH_A, PDF_C)["sha256"])};
      return [folder.record.amex.ids, [...await s.stillThere(root, folder, 'amex', ['o1', 'o2'])]];
    }}""")
    ids, there = run(tab, call, {}, {"a.json": one, "b.json": two})["result"]
    assert ids == ["o1"] and there == ["o1"]


def test_something_else_named_as_the_history_folder_is_a_folder_that_cannot_be_read(tab):
    call = f"""async (s, root) => {{
      const w = await (await root.getFileHandle('Statement Sweep History', {{ create: true }})).createWritable(); await w.write('x'); await w.close();
      return ({SAFE})(s.reconcile(root));
    }}"""
    got = run(tab, call)["result"]
    assert got["threw"] == "FolderUnavailable" and "Nothing was saved" in got["says"]


# -- what is kept of a file is what was read from it --------------------------------

VERIFY = "async (s, root) => s.verified(root, %s)"


@pytest.mark.parametrize("pdfs, there, why", [
    ({PATH_A: PDF_A}, True, "the bytes claimed"),
    ({PATH_A: PDF_C}, False, "other bytes of the same size, put there since"),
    ({PATH_A: PDF_B}, False, "another size"),
    ({}, False, "not there"),
])
def test_a_file_is_read_before_anything_is_kept_of_it(tab, pdfs, there, why):
    got = run(tab, with_pdfs(pdfs, VERIFY % json.dumps(facts(PATH_A, PDF_A))))["result"]
    assert (got is not None) == there, why
    if there:
        assert got["sha256"] == facts(PATH_A, PDF_A)["sha256"] and got["size"] == len(PDF_A) and got["mtime"] > 0


# -- writing -----------------------------------------------------------------------

def write(saved, when="2026-11-24T14:05:09"):
    return f"async (s, root) => s.writeEntry(root, 'amex', {json.dumps(saved)}, new Date({json.dumps(when)}))"


def test_an_entry_holds_only_the_documents_given_with_their_files_dates_and_tax_marks(tab):
    saved = [{"id": "a1", "facts": facts(PATH_A, PDF_A)}, {"id": "a2", "facts": facts("p/a2.pdf", PDF_B)}]
    got = run(tab, write(saved), STORE)
    assert NAME.match(got["result"]["name"]), got["result"]
    assert got["result"]["dates"] == {"a1": "2026-10-28", "a2": "2026-09-28"} and got["result"]["tax"] == ["a1"]
    file = json.loads(got["files"][got["result"]["name"]])
    assert file["sites"] == {"amex": ["a1", "a2"]}
    assert file["files"] == {"amex": {"a1": facts(PATH_A, PDF_A), "a2": facts("p/a2.pdf", PDF_B)}}
    assert file["dates"] == {"amex": {"a1": "2026-10-28", "a2": "2026-09-28"}}
    assert file["tax"] == {"amex": ["a1"]}
    # Never the settings: folder and file names and switched-off sites stay here.
    assert "naming" not in file and "off" not in file


def test_an_entry_reads_back_as_a_history_file(tab):
    got = run(tab, write([{"id": "a1", "facts": facts(PATH_A, PDF_A)}]), STORE)
    back = tab.evaluate("async (t) => (await import('/history.js')).readHistory(t)", got["files"][got["result"]["name"]])
    assert back["ok"] is True and back["sites"] == {"amex": ["a1"]} and back["files"] == {"amex": {"a1": facts(PATH_A, PDF_A)}}


def test_facts_of_a_saved_file_are_its_path_and_the_size_and_sha256_of_its_bytes(tab):
    # Taken from the bytes saved, not from whatever is at the path now.
    call = with_pdfs({PATH_A: PDF_B}, f"async (s, root) => s.factsOf({json.dumps(PATH_A)}, new Blob([{json.dumps(PDF_A)}]))")
    assert run(tab, call)["result"] == facts(PATH_A, PDF_A)


def test_entries_written_at_the_same_moment_never_write_over_each_other(tab):
    call = f"""async (s, root) => {{
      const when = new Date('2026-11-24T14:05:09');
      const one = {json.dumps([{"id": "a1", "facts": facts(PATH_A, PDF_A)}])};
      return (await Promise.all([s.writeEntry(root, 'amex', one, when), s.writeEntry(root, 'amex', one, when)])).map((w) => w.name);
    }}"""
    got = run(tab, call, STORE, {"2026-11-24 140509 old.json": entry()})
    assert len(set(got["result"])) == 2 and len(got["files"]) == 3
    assert got["files"]["2026-11-24 140509 old.json"] == entry()


def test_nothing_to_write_writes_nothing(tab):
    got = run(tab, write([]), STORE)
    assert got["result"] is None and got["files"] == {}


def test_more_documents_than_an_entry_can_hold_is_refused(tab):
    saved = [{"id": f"d{n}", "facts": facts(f"p/{n}.pdf", "x")} for n in range(20001)]
    call = f"async (s, root) => s.writeEntry(root, 'amex', {json.dumps(saved)}).then(() => 'written', (e) => e.message)"
    got = run(tab, call, {})
    assert "more than one history file can hold" in got["result"] and got["files"] == {}


def test_the_sites_checked_are_written_as_an_entry_of_their_own(tab):
    call = "async (s, root) => s.writeChecked(root, { chase: '2026-11-24', amex: '2026-11-24' }, new Date('2026-11-24T14:05:09'))"
    got = run(tab, call)
    assert NAME.match(got["result"])
    file = json.loads(got["files"][got["result"]])
    assert file["checked"] == {"chase": "2026-11-24", "amex": "2026-11-24"} and file["sites"] == {} and "files" not in file
    assert run(tab, "async (s, root) => s.writeChecked(root, {})")["files"] == {}


def test_what_one_copy_writes_another_reads_as_the_folders_record(tab):
    mine = run(tab, with_pdfs({PATH_A: PDF_A}, f"""async (s, root) => {{
      await s.writeEntry(root, 'amex', [{{ id: 'a1', facts: await s.factsOf({json.dumps(PATH_A)}, new Blob([{json.dumps(PDF_A)}])) }}]);
      await s.writeChecked(root, {{ amex: '2026-11-24' }});
    }}"""), STORE)
    got = run(tab, with_pdfs({PATH_A: PDF_A}, RECONCILE), {"seen:amex": ["z9"]}, mine["files"])
    assert got["result"]["record"]["amex"] == {"ids": ["a1"], "dates": {"a1": "2026-10-28"}, "tax": ["a1"], "paths": {"a1": PATH_A},
                                               "sums": {"a1": facts(PATH_A, PDF_A)["sha256"]}, "checked": "2026-11-24"}
    assert got["store"] == {"seen:amex": ["z9"]}


# -- a document already in the folder is not saved beside itself ----------------------

SAVE = """async (s, root, path, text) => (await import('/folders.js')).writeFile(root, path, new Blob([text]), { reuseSame: true })"""
LISTING = """async (root) => { const out = []; const dir = await (await root.getDirectoryHandle('Statement Sweep')).getDirectoryHandle('American Express');
  for await (const [n] of dir.entries()) out.push(n); return out.sort(); }"""


def saving(pdfs, text):
    return with_pdfs(pdfs, f"async (s, root) => [await ({SAVE})(s, root, {json.dumps(PATH_A)}, {json.dumps(text)}), await ({LISTING})(root)]")


def test_the_same_bytes_under_the_same_name_are_left_as_they_are(tab):
    path, names = run(tab, saving({PATH_A: PDF_A}, PDF_A))["result"]
    assert path == PATH_A and names == ["2026-10-28 American Express Statement.pdf"]


def test_other_bytes_under_the_same_name_are_saved_beside_it(tab):
    path, names = run(tab, saving({PATH_A: PDF_A}, PDF_C))["result"]
    assert path == PATH_A.replace(".pdf", " (1).pdf") and len(names) == 2


def test_the_same_bytes_as_a_numbered_copy_are_left_as_they_are(tab):
    numbered = PATH_A.replace(".pdf", " (1).pdf")
    path, names = run(tab, saving({PATH_A: PDF_A, numbered: PDF_C}, PDF_C))["result"]
    assert path == numbered and len(names) == 2


def test_the_same_bytes_under_another_name_in_the_folder_are_left_as_they_are(tab):
    # Another computer, or an older file name pattern, named the same document differently.
    other = "Statement Sweep/American Express/American Express 2026-10-28.pdf"
    path, names = run(tab, saving({other: PDF_A}, PDF_A))["result"]
    assert path == other and names == ["American Express 2026-10-28.pdf"]


def test_other_bytes_of_the_same_length_under_another_name_are_saved(tab):
    other = "Statement Sweep/American Express/American Express 2026-10-28.pdf"
    changed = PDF_A[:-1] + ("X" if PDF_A[-1] != "X" else "Y")
    path, names = run(tab, saving({other: changed}, PDF_A))["result"]
    assert path == PATH_A and len(names) == 2


def test_a_save_begun_and_never_finished_has_its_name_taken_over(tab):
    # An empty file is all an unfinished save leaves behind.
    path, names = run(tab, saving({PATH_A: ""}, PDF_A))["result"]
    assert path == PATH_A and names == ["2026-10-28 American Express Statement.pdf"]
    back = run(tab, with_pdfs({PATH_A: ""}, f"""async (s, root) => {{
      await ({SAVE})(s, root, {json.dumps(PATH_A)}, {json.dumps(PDF_A)});
      return s.verified(root, {json.dumps(facts(PATH_A, PDF_A))}).then(Boolean);
    }}"""))["result"]
    assert back is True


def test_two_different_documents_saved_at_once_under_one_name_both_survive(tab):
    # The first is still being written, and empty, when the second chooses its name.
    call = f"""async (s, root) => {{
      const f = await import('/folders.js');
      const path = {json.dumps(PATH_A)};
      const names = await Promise.all([f.writeFile(root, path, new Blob([{json.dumps(PDF_A)}]), {{ reuseSame: true }}),
                                       f.writeFile(root, path, new Blob([{json.dumps(PDF_B)}]), {{ reuseSame: true }})]);
      return [names, await ({LISTING})(root)];
    }}"""
    names, listing = run(tab, call)["result"]
    assert set(names) == {PATH_A, PATH_A.replace(".pdf", " (1).pdf")} and len(listing) == 2


def test_two_documents_of_one_size_saved_at_once_under_one_name_never_write_over_each_other(tab):
    # They cannot be told apart until the first is written, so the second waits for another time.
    call = f"""async (s, root) => {{
      const f = await import('/folders.js');
      const path = {json.dumps(PATH_A)};
      const save = (text) => f.writeFile(root, path, new Blob([text]), {{ reuseSame: true }}).then((v) => v, (e) => e.constructor.name);
      const names = await Promise.all([save({json.dumps(PDF_A)}), save({json.dumps(PDF_C)})]);
      const kept = await (await (await (await (await root.getDirectoryHandle('Statement Sweep')).getDirectoryHandle('American Express')).getFileHandle('2026-10-28 American Express Statement.pdf')).getFile()).text();
      return [names, kept, await ({LISTING})(root)];
    }}"""
    names, kept, listing = run(tab, call)["result"]
    assert names == [PATH_A, "StillSaving"] and kept == PDF_A and len(listing) == 1


def test_a_document_tried_again_while_its_first_save_has_not_ended_is_not_saved_beside_it(tab):
    # The first save hangs at the folder; the same bytes come again.
    call = f"""async (s, root) => {{
      const f = await import('/folders.js');
      const path = {json.dumps(PATH_A)};
      const never = new Promise(() => {{}});
      const hang = (dir) => ({{
        getDirectoryHandle: async (n, o) => hang(await dir.getDirectoryHandle(n, o)),
        getFileHandle: async (n, o) => {{ const h = await dir.getFileHandle(n, o); return {{ getFile: () => h.getFile(), createWritable: () => (o && o.create ? never : h.createWritable()) }}; }},
        entries: () => dir.entries(),
      }});
      f.writeFile(hang(root), path, new Blob([{json.dumps(PDF_A)}]), {{ reuseSame: true }});
      await new Promise((r) => setTimeout(r, 50));
      const again = await f.writeFile(root, path, new Blob([{json.dumps(PDF_A)}]), {{ reuseSame: true }}).then((v) => v, (e) => e.constructor.name + ': ' + e.message);
      const other = await f.writeFile(root, path, new Blob([{json.dumps(PDF_B)}]), {{ reuseSame: true }});
      return [again, other, await ({LISTING})(root)];
    }}"""
    again, other, listing = run(tab, call)["result"]
    assert again == "StillSaving: This document is still being saved from an earlier try. It is tried again next time."
    # A different document of that name is still saved, beside it.
    assert other == PATH_A.replace(".pdf", " (1).pdf") and len(listing) == 2


def test_without_asking_for_it_the_same_bytes_are_still_saved_beside(tab):
    call = with_pdfs({PATH_A: PDF_A}, f"async (s, root) => (await import('/folders.js')).writeFile(root, {json.dumps(PATH_A)}, new Blob([{json.dumps(PDF_A)}]))")
    assert run(tab, call)["result"] == PATH_A.replace(".pdf", " (1).pdf")


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


def test_choosing_a_folder_tells_the_engine_and_says_what_the_folder_holds(stab):
    settings(stab, {"seen:amex": ["a1"]}, {"state": "ok", "documents": 3, "entries": 2, "at": 1, "name": "Shared"})
    choose(stab, "Shared")
    stab.wait_for_function("document.getElementById('historyStatus').textContent !== ''")
    assert stab.evaluate("__sent") == [{"type": "folderChanged"}]
    assert stab.inner_text("#historyStatus") == "Your folder holds 3 documents saved by Statement Sweep, here or on another computer. They are not saved again."


def test_a_folder_that_cannot_be_read_says_so(stab):
    settings(stab, {}, {"state": "problem", "text": "Your folder could not be read. Nothing was saved."})
    choose(stab, "Shared")
    stab.wait_for_function("document.getElementById('historyProblem').textContent !== ''")
    assert stab.inner_text("#historyProblem") == "Your folder could not be read. Nothing was saved."


def test_use_downloads_and_the_history_file_switch_tell_the_engine_too(stab):
    settings(stab, {}, {"state": "none"})
    choose(stab, "Shared")
    stab.uncheck("#shareHistory")
    stab.wait_for_function("__sent.length === 2")
    stab.click("#useDownloads")
    stab.wait_for_function("__sent.length === 3")
    assert stab.evaluate("__sent") == [{"type": "folderChanged"}] * 3


def test_sharing_is_on_with_a_folder_and_can_be_turned_off(stab):
    settings(stab, {})
    assert stab.locator("#shareRow").is_hidden()
    choose(stab, "Shared")
    assert stab.locator("#shareRow").is_visible() and stab.is_checked("#shareHistory")
    stab.uncheck("#shareHistory")
    stab.wait_for_function("__store.historySharingOff === true")
    stab.check("#shareHistory")
    stab.wait_for_function("__store.historySharingOff === false")


def renaming(stab, answer, yes=True):
    settings(stab, {}, {"state": "ok", "documents": 3, "name": "Shared"})
    assert stab.locator("#renameRow").is_hidden()
    choose(stab, "Shared")
    stab.wait_for_function("__sent.length === 1")
    assert stab.locator("#renameRow").is_visible()
    stab.evaluate("([answer, yes]) => { window.__answer = answer; window.__asked = []; window.confirm = (t) => { __asked.push(t); return yes; }; }", [answer, yes])
    stab.click("#renameFiles")
    stab.wait_for_function("document.getElementById('renameStatus').textContent !== '' && !document.getElementById('renameFiles').disabled")
    return stab.evaluate("__sent.slice(1)"), stab.evaluate("__asked"), stab.inner_text("#renameStatus")


def test_renaming_says_what_it_will_do_and_asks_before_it_does(stab):
    sent, asked, status = renaming(stab, {"state": "ok", "count": 2, "alone": 1, "renamed": 2, "skipped": 0, "problem": ""})
    assert sent == [{"type": "renamePreview"}, {"type": "renameFiles"}]
    assert asked == ["Rename 2 documents in your folder to match the folder and file name settings? 1 document whose title is not known is left as it is."]
    assert status == "Renamed 2 documents."


def test_renaming_not_agreed_to_changes_nothing(stab):
    sent, asked, status = renaming(stab, {"state": "ok", "count": 2, "alone": 0}, yes=False)
    assert sent == [{"type": "renamePreview"}] and len(asked) == 1 and status == "Nothing was renamed."


def test_with_every_name_right_there_is_nothing_to_ask(stab):
    sent, asked, status = renaming(stab, {"state": "ok", "count": 0, "alone": 0})
    assert sent == [{"type": "renamePreview"}] and asked == []
    assert status == "Every document already matches the folder and file name settings."


def test_renaming_during_a_sync_waits_for_it(stab):
    sent, asked, status = renaming(stab, {"busy": True})
    assert sent == [{"type": "renamePreview"}] and asked == [] and status == "A sync is running. Rename once it finishes."


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
