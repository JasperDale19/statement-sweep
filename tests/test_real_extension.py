"""The shared folder, read by the real extension: the unpacked extension
loaded in Chromium, with its own service worker, Settings page and popup,
and no stand-in for Chrome's extension APIs. The folder is one in the
extension's own private storage, since a test cannot press a folder picker.
Every id and date is invented."""
import hashlib
import json
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright
from conftest import OFFLINE

EXTENSION = str(Path(__file__).resolve().parents[1] / "extension")
PDF = "%PDF-1.4 invented\n%%EOF"
PATH = "Statement Sweep/American Express/2026-10-28 American Express Statement.pdf"
ENTRY = json.dumps({"format": "statement-sweep-history", "version": 1, "sites": {"amex": ["o1"]}, "dates": {"amex": {"o1": "2026-10-28"}},
                    "files": {"amex": {"o1": {"path": PATH, "size": len(PDF), "sha256": hashlib.sha256(PDF.encode()).hexdigest()}}}})

# A folder with one document another computer saved and its entry, chosen as
# Settings would; then the engine is told, as Settings tells it.
CHOOSE = """async ({ path, pdf, entry }) => {
  const root = await navigator.storage.getDirectory();
  const chosen = await root.getDirectoryHandle('Shared', { create: true });
  const put = async (p, body) => {
    const parts = p.split('/'); const name = parts.pop(); let dir = chosen;
    for (const part of parts) dir = await dir.getDirectoryHandle(part, { create: true });
    const w = await (await dir.getFileHandle(name, { create: true })).createWritable(); await w.write(body); await w.close();
  };
  await put(path, pdf);
  await put('Statement Sweep History/theirs.json', entry);
  await (await import('./folders.js')).setFolder('destination', chosen);
  return chrome.runtime.sendMessage({ type: 'folderChanged' });
}"""


@pytest.fixture
def extension(tmp_path):
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(tmp_path / "profile"), channel="chromium", headless=True,
            args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}", OFFLINE])
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker", timeout=15000)
        yield context, worker.url.split("/")[2]
        context.close()


def test_the_real_engine_reads_a_shared_folder_and_the_popup_shows_it(extension):
    context, ext = extension
    page = context.new_page()
    page.goto(f"chrome-extension://{ext}/options.html")
    page.wait_for_selector("#scope option[value=amex]", state="attached")
    answer = page.evaluate(CHOOSE, {"path": PATH, "pdf": PDF, "entry": ENTRY})
    assert answer["state"] == "ok" and answer["documents"] == 1 and answer["name"] == "Shared", answer
    kept = page.evaluate("chrome.storage.local.get(['folder:amex', 'seen:amex'])")
    # In the folder's record, and never in this copy's own.
    assert kept == {"folder:amex": {"ids": ["o1"], "dates": {"o1": "2026-10-28"}, "tax": [], "checked": ""}}
    popup = context.new_page()
    popup.goto(f"chrome-extension://{ext}/popup.html")
    popup.wait_for_function("document.getElementById('folderLine').textContent.startsWith('In sync')")
    assert popup.inner_text("#folderLine") == "In sync with “Shared”: 1 document saved, on this computer or another."
    # The file goes: the next read no longer counts it, and nothing is left behind.
    page.evaluate("""async (path) => {
      const parts = path.split('/'); const name = parts.pop();
      let dir = await (await navigator.storage.getDirectory()).getDirectoryHandle('Shared');
      for (const part of parts) dir = await dir.getDirectoryHandle(part);
      await dir.removeEntry(name);
    }""", PATH)
    again = page.evaluate("chrome.runtime.sendMessage({ type: 'folderChanged' })")
    assert again["state"] == "ok" and again["documents"] == 0
    assert page.evaluate("chrome.storage.local.get('folder:amex')") == {}


LISTING = """async () => {
  const out = [];
  async function walk(d, p) { for await (const [n, h] of d.entries()) { if (h.kind === 'directory') await walk(h, p + n + '/'); else if (n.endsWith('.pdf')) out.push(p + n); } }
  await walk(await (await navigator.storage.getDirectory()).getDirectoryHandle('Shared'), '');
  return out.sort();
}"""


def test_the_real_engine_renames_what_is_saved_to_the_settings_of_now(extension):
    context, ext = extension
    page = context.new_page()
    page.goto(f"chrome-extension://{ext}/options.html")
    page.wait_for_selector("#scope option[value=amex]", state="attached")
    assert page.evaluate(CHOOSE, {"path": PATH, "pdf": PDF, "entry": ENTRY})["state"] == "ok"
    page.evaluate("chrome.storage.local.set({ naming: { base: 'Statement Sweep', folder: '{provider}', file: '{provider} {date}[ {title}]', providers: {} } })")
    assert page.evaluate("chrome.runtime.sendMessage({ type: 'renamePreview' })") == {"state": "ok", "count": 1, "alone": 0, "old": 0}
    assert page.evaluate(LISTING) == [PATH]
    done = page.evaluate("chrome.runtime.sendMessage({ type: 'renameFiles' })")
    assert done["state"] == "ok" and done["renamed"] == 1 and done["problem"] == "", done
    renamed = "Statement Sweep/American Express/American Express 2026-10-28 Statement.pdf"
    assert page.evaluate(LISTING) == [renamed]
    # Still one document in the folder's record, found in its new place, with nothing left to rename.
    assert page.evaluate("chrome.storage.local.get('folder:amex')")["folder:amex"]["ids"] == ["o1"]
    assert page.evaluate("chrome.runtime.sendMessage({ type: 'renamePreview' })") == {"state": "ok", "count": 0, "alone": 0, "old": 0}
