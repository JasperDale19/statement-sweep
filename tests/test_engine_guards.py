"""The engine's (background.js) guards against a site that misbehaves, with
test_log.py's stand-in for Chrome's extension APIs and a stand-in recipe:
only a PDF counts as saved, a page or a save that never answers is given up
on and the run still ends, Stop cuts a list read short, a list is kept
within bounds, a document's id is logged without its query, a run is asked
for only from the extension's own pages, and Add Site leaves a built-in
site's host alone. Every number, address and account here is invented."""
import hashlib
import json
import re
import pytest
from playwright.sync_api import sync_playwright
from test_log import ENGINE, PROVIDER, page_with
from test_added_engine import ADD, FROM_TAB, MORE, PICKED

INDEX = "export const PROVIDERS = " + json.dumps([PROVIDER]) + ";\n"
PDF = "%PDF-1.4 invented\n%%EOF"

# Does what window.__plan says: lists its docs, hands over its bodies, or
# never answers.
RECIPE = """(() => {
  const plan = window.__plan;
  const never = () => new Promise(() => {});
  const readList = async () => {
    if (plan.hangList) return never();
    if (plan.removeOnList) {
      const parts = plan.removeOnList.split('/'); const name = parts.pop();
      let dir = await navigator.storage.getDirectory();
      for (const part of parts) dir = await dir.getDirectoryHandle(part);
      await dir.removeEntry(name);
    }
    return { state: 'ok', docs: plan.docs, more: Boolean(plan.more) };
  };
  const fetchPdf = async (doc) => {
    if (plan.hangFetch) return never();
    if (plan.switchFolder && !plan.switched) {
      plan.switched = true;
      const root = await navigator.storage.getDirectory();
      await (await import('/folders.js')).setFolder('destination', await root.getDirectoryHandle(plan.switchFolder, { create: true }));
    }
    return { ok: true, base64: btoa((plan.bodies || {})[doc.id] || '%PDF-1.4 invented\\n%%EOF') };
  };
  const nextView = () => (plan.hangNext ? never() : { moved: false });
  const clickDoc = (doc) => { setTimeout(() => window.__download(doc), 10); return { ok: true }; };
  globalThis.__sweep = { readList, fetchPdf, nextView, clickDoc };
})();"""

# The engine's waits of two and five minutes cut short when __shrink is set,
# a download the page starts (written to the inbox as Chrome would), and
# which downloads were taken out again.
EXTRA = """
window.__on = on;
const realTimeout = window.setTimeout;
window.__realTimeout = realTimeout;
// Two minutes become 300 ms, and five or more 3 s, so a page call still ends before its save.
window.setTimeout = (f, ms, ...rest) => realTimeout(f, !window.__shrink || ms < 120000 ? ms : ms < 300000 ? 300 : 3000, ...rest);
chrome.downloads.removeFile = async (id) => { __events.push(['removeFile', id]); };
chrome.downloads.erase = async ({ id }) => { __events.push(['erase', id]); };
// Settings switching to another folder while the engine reads the shared one.
const sessionSet = chrome.storage.session.set;
chrome.storage.session.set = async (o) => {
  const plan = window.__plan || {};
  if (plan.switchWhileChecking && !plan.switchedEarly && o.folderSync && o.folderSync.state === 'checking') {
    plan.switchedEarly = true;
    const root = await navigator.storage.getDirectory();
    await (await import('/folders.js')).setFolder('destination', await root.getDirectoryHandle(plan.switchWhileChecking, { create: true }));
  }
  return sessionSet(o);
};
// A download the engine starts itself, finished at once, when the plan says so.
const standInDownload = chrome.downloads.download;
chrome.downloads.download = async (o) => {
  if (!(window.__plan || {}).finishDownloads) return standInDownload(o);
  __events.push(['download', o.filename]);
  realTimeout(() => on.dl.set.forEach((g) => g({ id: 91, state: { current: 'complete' } })), 0);
  return 91;
};
window.__download = () => {
  const id = 77;
  const item = { id, url: 'https://bank.test/files/s.pdf', finalUrl: 'https://bank.test/files/s.pdf', referrer: 'https://bank.test/docs', mime: 'application/pdf', filename: 's.pdf' };
  for (const f of on.name.set) f(item, async (s) => {
    if (!s) return;
    const inbox = await (await import('/folders.js')).getFolder('inbox');
    const file = await inbox.getFileHandle(s.filename.split('/').pop(), { create: true });
    const w = await file.createWritable(); await w.write(window.__plan.clickBody); await w.close();
    on.dl.set.forEach((g) => g({ id, state: { current: 'complete' } }));
  });
};
"""

START = """async ({ plan, local, shrink, stopWhen, sender, shared }) => {
  window.__plan = plan; window.__shrink = shrink;
  Object.assign(__local, local || {});
  const f = await import('/folders.js');
  const root = await navigator.storage.getDirectory();
  const chosen = await root.getDirectoryHandle('Chosen', { create: true });
  await f.setFolder('destination', chosen);
  await f.setFolder('inbox', await root.getDirectoryHandle('Inbox', { create: true }));
  // A folder where the document would go, so the chosen folder cannot take it.
  for (const path of plan.blockPaths || []) {
    let dir = chosen;
    for (const part of path.split('/')) dir = await dir.getDirectoryHandle(part, { create: true });
  }
  // Other copies' history files, already in the chosen folder, and any
  // file named by a path, such as a document another copy saved.
  for (const [name, text] of Object.entries(shared || {})) {
    const parts = (name.includes('/') ? name : 'Statement Sweep History/' + name).split('/');
    const leaf = parts.pop();
    let dir = chosen;
    for (const part of parts) dir = await dir.getDirectoryHandle(part, { create: true });
    const w = await (await dir.getFileHandle(leaf, { create: true })).createWritable();
    await w.write(text); await w.close();
  }
  await import('/background.js');
  __on.message.set.forEach((g) => g({ type: 'sweep', only: 'fake' }, sender || __popup, () => {}));
  const began = Date.now();
  let stopped = false;
  for (;;) {
    const line = (__session.board || {}).fake;
    if (stopWhen && !stopped && line && line.state === stopWhen && Date.now() - began > 300) {
      stopped = true;
      __on.message.set.forEach((g) => g({ type: 'stop' }, __popup, () => {}));
    }
    const out = {};
    async function walk(d, p) { for await (const [n, h] of d.entries()) {
      if (h.kind === 'directory') await walk(h, p + n + '/');
      else { try { out[p + n] = await (await h.getFile()).text(); } catch (e) { if (!['NotFoundError', 'NotReadableError'].includes(e.name)) throw e; } } } }
    await walk(root, '');
    const summary = Object.keys(out).find((p) => /^Chosen\\/Statement Sweep\\/Logs\\/run-\\d{8}-\\d{6}\\.log$/.test(p));
    if ((summary && out[summary].includes('Run ended')) || Date.now() - began > (sender ? 1500 : 25000)) {
      return { files: out, board: __session.board, session: __session, local: __local, events: __events, ms: Date.now() - began };
    }
    await new Promise((r) => __realTimeout(r, 100));
  }
}"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def engine(browser):
    t = page_with(browser, ENGINE + EXTRA, {"providers/index.js": INDEX, "providers/fake.page.js": RECIPE})
    yield t
    t.context_.close()


def doc(id, method="fetch", date="2026-10-28"):
    return {"id": id, "date": date, "title": "Statement", "method": method}


def sweep(engine, plan, local=None, shrink=False, stop_when=None, sender=None, shared=None):
    got = engine.evaluate(START, {"plan": plan, "local": local or {}, "shrink": shrink, "stopWhen": stop_when, "sender": sender, "shared": shared or {}})
    site = next((v for k, v in got["files"].items() if re.match(r"Chosen/Statement Sweep/Fake Bank/Logs/run-\d{8}-\d{6}\.log$", k)), "")
    # The inbox is Downloads, where the stand-in for Chrome never removes a file.
    pdfs = [k for k in got["files"] if k.endswith(".pdf") and not k.startswith("Inbox/")]
    return got, site, pdfs


# -- only a PDF is saved -------------------------------------------------------

def test_a_fetched_document_that_is_not_a_pdf_is_not_saved_or_marked_seen(engine):
    got, site, pdfs = sweep(engine, {"docs": [doc("f1"), doc("f2", date="2026-09-28")], "bodies": {"f2": "<html>Sign in</html>"}})
    assert got["board"]["fake"]["state"] == "ok"
    assert "1 other did not save" in got["board"]["fake"]["text"] and "not a PDF" in got["board"]["fake"]["text"]
    assert got["local"]["seen:fake"] == ["f1"]
    assert pdfs == ["Chosen/Statement Sweep/Fake Bank/2026-10-28 Fake Statement.pdf"]
    assert "Document f2 by fetch: not saved: The site sent something that is not a PDF." in site


def test_a_download_that_is_not_a_pdf_is_not_saved_and_is_taken_out_of_downloads(engine):
    got, site, pdfs = sweep(engine, {"docs": [doc("c1", "click")], "clickBody": "<html>Sign in</html>"})
    assert got["board"]["fake"]["state"] == "bad"
    assert "not a PDF" in got["board"]["fake"]["text"]
    assert "seen:fake" not in got["local"]
    assert pdfs == []
    assert ["removeFile", 77] in got["events"] and ["erase", 77] in got["events"]


def test_a_download_that_is_a_pdf_is_still_moved_and_marked_seen(engine):
    got, _, pdfs = sweep(engine, {"docs": [doc("c1", "click")], "clickBody": PDF})
    assert got["board"]["fake"]["text"] == "1 new.", got["board"]
    assert got["local"]["seen:fake"] == ["c1"]
    assert pdfs == ["Chosen/Statement Sweep/Fake Bank/2026-10-28 Fake Statement.pdf"]
    assert got["files"][pdfs[0]] == PDF


# -- the folder shared with other computers ----------------------------------------

HIST = "Chosen/Statement Sweep History/"
F1 = "Statement Sweep/Fake Bank/2026-09-28 Fake Statement.pdf"
F2 = "Statement Sweep/Fake Bank/2026-10-28 Fake Statement.pdf"
OTHER_PDF = "%PDF-1.4 something else\n%%EOF"


def facts(path, text=PDF):
    return {"path": path, "size": len(text.encode()), "sha256": hashlib.sha256(text.encode()).hexdigest()}


def other_copy(ids, path=F1, **over):
    return json.dumps({"format": "statement-sweep-history", "version": 1, "sites": {"fake": ids},
                       "dates": {"fake": {i: "2026-09-28" for i in ids}},
                       "files": {"fake": {i: facts(path) for i in ids}}, **over})


# The entries this run wrote: those of documents, and those of sites checked.
def written(got, given=("theirs.json",)):
    all_ = [json.loads(v) for k, v in got["files"].items() if k.startswith(HIST) and k[len(HIST):] not in given]
    return [e for e in all_ if "files" in e], [e for e in all_ if "checked" in e]


def summary_of(got):
    return next(v for k, v in got["files"].items() if re.match(r"Chosen/Statement Sweep/Logs/run-", k))


def test_a_document_another_computer_saved_into_the_folder_is_not_saved_again(engine):
    got, site, pdfs = sweep(engine, {"docs": [doc("f1", date="2026-09-28"), doc("f2")]},
                            shared={"theirs.json": other_copy(["f1"]), F1: PDF})
    assert got["board"]["fake"]["text"] == "1 new.", got["board"]
    assert sorted(pdfs) == ["Chosen/" + F1, "Chosen/" + F2]
    # Only what this copy saved is in its own record.
    assert got["local"]["seen:fake"] == ["f2"]
    # A new entry, holding only what this run saved into the folder, and how to check it.
    docs, checked = written(got)
    assert [e["sites"] for e in docs] == [{"fake": ["f2"]}] and docs[0]["files"] == {"fake": {"f2": facts(F2)}}
    assert got["files"][HIST + "theirs.json"] == other_copy(["f1"])
    assert re.search(r"Folder read before the run: 1 history file\(s\), 1 document\(s\) in the folder, 1 file\(s\) read to check them, in [\d,]+ ms\.", summary_of(got))
    assert "1 already in your chosen folder, saved by another computer." in site
    assert "History entry written: 1 document(s)" in site


def test_what_the_folder_holds_is_kept_for_the_popup_and_the_schedule(engine):
    got, _, _ = sweep(engine, {"docs": [doc("f1", date="2026-09-28"), doc("f2")]}, shared={"theirs.json": other_copy(["f1"]), F1: PDF})
    kept = got["local"]["folder:fake"]
    # The other computer's document, and the one just saved here.
    assert kept["ids"] == ["f1", "f2"] and kept["dates"] == {"f1": "2026-09-28", "f2": "2026-10-28"}
    assert re.match(r"\d{4}-\d\d-\d\d$", kept["checked"])
    assert "paths" not in kept


def test_a_site_looked_at_with_nothing_left_writes_that_it_was_checked(engine):
    got, _, pdfs = sweep(engine, {"docs": [doc("f1")]}, local={"seen:fake": ["f1"]})
    docs, checked = written(got)
    assert pdfs == [] and docs == []
    assert len(checked) == 1 and list(checked[0]["checked"]) == ["fake"] and checked[0]["sites"] == {}
    assert "History entry written: 1 site(s) checked" in summary_of(got)


def test_a_site_another_computer_checked_is_known_here(engine):
    theirs = json.dumps({"format": "statement-sweep-history", "version": 1, "sites": {}, "checked": {"fake": "2026-11-20"}})
    got, _, _ = sweep(engine, {"docs": [], "hangList": True}, shared={"theirs.json": theirs}, stop_when="working")
    assert got["local"]["folder:fake"]["checked"] == "2026-11-20"


def test_a_document_another_computer_claims_but_whose_file_is_not_here_is_saved(engine):
    # Its entry synced down before its PDF did, or the PDF never uploaded.
    got, _, pdfs = sweep(engine, {"docs": [doc("f1", date="2026-09-28"), doc("f2")]}, shared={"theirs.json": other_copy(["f1"])})
    assert got["board"]["fake"]["text"] == "2 new.", got["board"]
    assert sorted(got["local"]["seen:fake"]) == ["f1", "f2"]
    assert "Chosen/" + F1 in pdfs


def test_a_claim_about_a_different_file_at_that_path_is_not_trusted(engine):
    got, _, pdfs = sweep(engine, {"docs": [doc("f1", date="2026-09-28")]}, shared={"theirs.json": other_copy(["f1"]), F1: OTHER_PDF})
    assert got["board"]["fake"]["text"] == "1 new."
    assert got["local"]["seen:fake"] == ["f1"]
    assert "Chosen/" + F1.replace(".pdf", " (1).pdf") in pdfs


def test_a_document_removed_after_the_folder_was_read_is_saved(engine):
    # Gone from the folder while the site was being read.
    got, site, pdfs = sweep(engine, {"docs": [doc("f1", date="2026-09-28")], "removeOnList": "Chosen/" + F1},
                            shared={"theirs.json": other_copy(["f1"]), F1: PDF})
    assert got["board"]["fake"]["text"] == "1 new.", got["board"]
    assert got["local"]["seen:fake"] == ["f1"] and "Chosen/" + F1 in pdfs
    assert "already in your chosen folder" not in site


def test_a_folder_that_cannot_be_read_saves_nothing_and_says_why(engine):
    # History from a newer Statement Sweep: what it lists is not known here.
    got, _, pdfs = sweep(engine, {"docs": [doc("f1"), doc("f2", date="2026-09-28")]}, shared={"theirs.json": other_copy(["f1"], version=2)})
    line = got["board"]["fake"]
    assert line["state"] == "bad" and line["text"].startswith("Nothing was saved. Your folder holds history from a newer Statement Sweep.")
    assert pdfs == [] and "seen:fake" not in got["local"]
    assert got["session"]["folderSync"]["state"] == "problem"


def test_a_history_file_whose_words_are_wrong_is_logged_and_the_run_goes_on(engine):
    got, _, _ = sweep(engine, {"docs": [doc("f1")]}, shared={"bad.json": "{nope"})
    assert got["board"]["fake"]["text"] == "1 new."
    assert "WARN   run: History file bad.json not used: This is not a Statement Sweep history file. It could not be read." in summary_of(got)
    assert got["files"][HIST + "bad.json"] == "{nope"


def test_with_sharing_off_the_folder_history_is_neither_read_nor_written(engine):
    got, _, _ = sweep(engine, {"docs": [doc("f1", date="2026-09-28")]},
                      local={"historySharingOff": True}, shared={"theirs.json": other_copy(["f1"]), F1: PDF})
    assert got["board"]["fake"]["text"] == "1 new."
    assert written(got) == ([], []) and "folder:fake" not in got["local"]


def test_a_shared_folder_that_cannot_be_written_stops_the_site_and_nothing_goes_to_downloads(engine):
    got, _, _ = sweep(engine, {"docs": [doc("f1"), doc("f2", date="2026-09-28")], "blockPaths": [F2], "finishDownloads": True})
    line = got["board"]["fake"]
    assert line["state"] == "bad" and "Your folder could not be written, so this site stops here." in line["text"]
    assert "seen:fake" not in got["local"]
    assert not [e for e in got["events"] if e[0] == "download"]


def test_a_folder_not_shared_that_cannot_be_written_still_saves_to_downloads(engine):
    got, _, _ = sweep(engine, {"docs": [doc("f1")], "blockPaths": [F2], "finishDownloads": True}, local={"historySharingOff": True})
    assert got["board"]["fake"]["state"] == "ok" and "saved in Downloads" in got["board"]["fake"]["text"]
    assert got["local"]["seen:fake"] == ["f1"] and ["download", F2] in got["events"]


def test_a_folder_chosen_during_a_sweep_is_used_from_the_next_one(engine):
    # Settings switches to another folder while the first document is saving.
    got, _, pdfs = sweep(engine, {"docs": [doc("f1"), doc("f2", date="2026-09-28")], "switchFolder": "Other"})
    assert got["board"]["fake"]["text"] == "2 new.", got["board"]
    assert sorted(pdfs) == ["Chosen/" + F1, "Chosen/" + F2]
    # The entry is in the folder the documents went to, and nothing is in the new one.
    assert [e["sites"] for e in written(got)[0]] == [{"fake": ["f1", "f2"]}]
    # Only the run's logs follow the folder chosen now, as they always have.
    assert not [k for k in got["files"] if k.startswith("Other/") and "/Logs/" not in k]


def test_what_is_saved_is_noted_until_its_entry_is_written(engine):
    got, _, _ = sweep(engine, {"docs": [doc("f1")]})
    assert len(written(got)[0]) == 1
    # Written, so nothing is left waiting.
    assert "pending:fake" not in got["local"]


def test_documents_a_run_cut_short_saved_are_written_to_the_history_by_the_next(engine):
    # Saved and marked as saved, and the browser closed before the entry was written.
    waiting = [{"id": "f1", "facts": facts(F1)}, {"id": "gone", "facts": facts("Statement Sweep/Fake Bank/never.pdf")}]
    got, _, pdfs = sweep(engine, {"docs": [doc("f1", date="2026-09-28")]},
                         local={"seen:fake": ["f1", "gone"], "dates:fake": {"f1": "2026-09-28"}, "pending:fake": waiting}, shared={F1: PDF})
    docs, _ = written(got)
    assert [e["files"] for e in docs] == [{"fake": {"f1": facts(F1)}}]
    assert "pending:fake" not in got["local"]
    assert got["local"]["folder:fake"]["ids"] == ["f1"]
    assert "1 document(s) saved in a run before this one are written to the folder's history now." in summary_of(got)


def test_a_folder_changed_while_the_shared_one_was_read_saves_nothing(engine):
    # What was read is the old folder's record, so it must not decide for the new one.
    got, _, pdfs = sweep(engine, {"docs": [doc("f1")], "switchWhileChecking": "Other"}, shared={"theirs.json": other_copy(["f9"])})
    line = got["board"]["fake"]
    assert line["state"] == "bad" and line["text"] == "Nothing was saved. Your folder was changed, or could not be read. Run it again."
    assert not [k for k in got["files"] if k.endswith(".pdf")] and "seen:fake" not in got["local"]


def test_a_document_already_in_the_folder_with_the_same_bytes_is_not_saved_beside_itself(engine):
    # In the folder, and in no entry: saved before sharing began, say.
    got, site, pdfs = sweep(engine, {"docs": [doc("f1", date="2026-09-28")]}, shared={F1: PDF})
    assert got["board"]["fake"]["text"] == "1 new."
    assert pdfs == ["Chosen/" + F1]
    assert [e["files"] for e in written(got)[0]] == [{"fake": {"f1": facts(F1)}}]


def test_a_document_saved_beside_another_of_its_name_is_listed_where_it_went(engine):
    got, site, pdfs = sweep(engine, {"docs": [doc("f1", date="2026-09-28")]}, shared={F1: OTHER_PDF})
    beside = F1.replace(".pdf", " (1).pdf")
    assert "Chosen/" + beside in pdfs
    assert [e["files"] for e in written(got)[0]] == [{"fake": {"f1": facts(beside)}}]


def test_a_document_saved_through_downloads_is_listed_with_its_own_bytes(engine):
    got, _, pdfs = sweep(engine, {"docs": [doc("c1", "click")], "clickBody": PDF})
    assert got["board"]["fake"]["text"] == "1 new."
    assert [e["files"] for e in written(got)[0]] == [{"fake": {"c1": facts(F2)}}]


ASK = """async ({ text, pdf, types }) => {
  const f = await import('/folders.js');
  const root = await navigator.storage.getDirectory();
  const chosen = await root.getDirectoryHandle('Chosen', { create: true });
  await f.setFolder('destination', chosen);
  const put = async (dir, name, body) => { const w = await (await dir.getFileHandle(name, { create: true })).createWritable(); await w.write(body); await w.close(); };
  await put(await chosen.getDirectoryHandle('Statement Sweep History', { create: true }), 'theirs.json', text);
  let dir = chosen;
  for (const part of ['Statement Sweep', 'Fake Bank']) dir = await dir.getDirectoryHandle(part, { create: true });
  await put(dir, '2026-09-28 Fake Statement.pdf', pdf);
  await import('/background.js');
  const ask = (type, sender) => new Promise((resolve) => {
    let kept = false;
    for (const g of __on.message.set) if (g({ type }, sender, resolve) === true) kept = true;
    if (!kept) resolve('no answer');
  });
  const out = [];
  for (const [type, who] of types) out.push(await ask(type, who === 'site' ? { id: 'self', url: 'https://bank.test/docs', tab: { id: 3 }, frameId: 0 } : __popup));
  return { out, local: __local, session: __session };
}"""


def test_the_popup_and_settings_can_ask_for_the_folder_to_be_read(engine):
    got = engine.evaluate(ASK, {"text": other_copy(["f1"]), "pdf": PDF, "types": [["syncHistory", "site"], ["folderChanged", "site"], ["syncHistory", "popup"], ["syncHistory", "popup"]]})
    assert got["out"][:2] == ["no answer", "no answer"]
    assert got["out"][2]["state"] == "ok" and got["out"][2]["documents"] == 1
    # Asked again at once: what was just read is answered, not read again.
    assert got["out"][3]["at"] == got["out"][2]["at"]
    assert got["local"]["folder:fake"]["ids"] == ["f1"] and "seen:fake" not in got["local"]
    assert got["session"]["folderSync"]["state"] == "ok"


def test_a_change_in_settings_forgets_what_was_read_and_reads_again(engine):
    got = engine.evaluate(ASK, {"text": other_copy(["f1"]), "pdf": PDF, "types": [["syncHistory", "popup"], ["folderChanged", "popup"]]})
    assert got["out"][1]["state"] == "ok" and got["out"][1]["at"] >= got["out"][0]["at"]
    assert got["local"]["folder:fake"]["ids"] == ["f1"]


def test_with_sharing_off_a_change_in_settings_clears_what_was_kept(engine):
    got = engine.evaluate("""async (a) => { __local['folder:fake'] = { ids: ['old'], dates: {}, tax: [], checked: '' }; __local.historySharingOff = true; return (""" + ASK + """)(a); }""",
                          {"text": other_copy(["f1"]), "pdf": PDF, "types": [["folderChanged", "popup"]]})
    assert got["out"][0] == {"state": "none"} and "folder:fake" not in got["local"]


# -- waits that never end --------------------------------------------------------

def test_a_save_whose_page_never_answers_is_given_up_and_the_run_ends(engine):
    got, site, _ = sweep(engine, {"docs": [doc("f1")], "hangFetch": True}, shrink=True)
    assert got["board"]["fake"]["state"] == "bad"
    assert "The page did not answer within 2 minutes." in got["board"]["fake"]["text"]
    assert "seen:fake" not in got["local"]


def test_a_next_view_that_never_answers_is_given_up_and_the_run_ends(engine):
    got, site, _ = sweep(engine, {"docs": [], "more": True, "hangNext": True}, shrink=True)
    assert got["board"]["fake"]["state"] == "bad"
    assert got["board"]["fake"]["text"] == "That did not finish. The page did not answer within 2 minutes."


def test_stop_cuts_short_a_list_read_that_never_answers(engine):
    got, _, _ = sweep(engine, {"hangList": True}, stop_when="working")
    assert got["board"]["fake"]["state"] == "bad"
    assert "You stopped the sync." in got["board"]["fake"]["text"]
    # Long before the page call's own two minutes.
    assert got["ms"] < 10000


# -- a list within bounds, and what is logged of it -----------------------------

def test_an_id_too_long_is_left_unread_and_a_query_in_an_id_is_not_logged(engine):
    long_id = "/docs/" + "x" * 300
    got, site, pdfs = sweep(engine, {"docs": [doc(long_id), doc("/docs/view?docId=123")]})
    assert got["board"]["fake"]["text"] == "1 new.", got["board"]
    # The id itself is kept as the site gave it: the record depends on it.
    assert got["local"]["seen:fake"] == ["/docs/view?docId=123"]
    assert "WARN   fake: 1 document(s) left unread: an id longer than 300 characters." in site
    assert "fake: Document /docs/view by fetch: saved" in site
    assert "docId" not in site and "x" * 300 not in site


def test_a_list_gives_at_most_five_thousand_documents(engine):
    ids = [f"d{i}" for i in range(5001)]
    got, site, pdfs = sweep(engine, {"docs": [doc(i) for i in ids]}, local={"seen:fake": ids[:5000]})
    assert "WARN   fake: The list held 5,001 documents; only the first 5,000 are taken." in site
    assert got["board"]["fake"]["text"] == "Nothing new."
    assert pdfs == []


# -- who may ask for a run -------------------------------------------------------

@pytest.mark.parametrize("why,sender", [
    ("no sender", {}),
    ("a site's tab", {"id": "self", "url": "https://bank.test/docs", "tab": {"id": 3}, "frameId": 0}),
    ("another extension", {"id": "someone-else", "url": "chrome-extension://someone-else/popup.html"}),
])
def test_a_run_is_asked_for_only_from_the_extensions_own_pages(engine, why, sender):
    got, _, _ = sweep(engine, {"docs": [doc("f1")]}, sender=sender)
    assert not (got["board"] or {}).get("fake"), why
    assert "seen:fake" not in got["local"]


def test_a_message_with_nothing_in_it_is_ignored(engine):
    assert engine.evaluate("""async () => {
      await import('/background.js');
      for (const g of __on.message.set) g(null, __popup, () => {});
      return true;
    }""") is True


# -- Add Site and a built-in site's host ---------------------------------------------

BUILT_IN = {**PROVIDER, "id": "acme-builtin", "name": "Acme Built In", "match": ["https://x.test/*"]}


@pytest.fixture
def bare(browser):
    t = page_with(browser, ENGINE + MORE, {"providers/index.js": "export const PROVIDERS = " + json.dumps([BUILT_IN]) + ";\n"})
    yield t
    t.context_.close()


def test_add_site_leaves_a_built_in_sites_host_alone(bare):
    got = bare.evaluate(ADD, {"config": PICKED, "sender": FROM_TAB})
    assert got["answer"] == {"ok": False, "why": "Acme Built In is already built in to Statement Sweep, so it isn't added again."}
    assert "addedSites" not in got["local"]
