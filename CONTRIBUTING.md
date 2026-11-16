# Contributing

Thank you for helping. Statement Sweep runs inside people's bank, card,
brokerage and tax accounts, so the rules below are about keeping those
accounts safe first and code style second. A pull request that breaks one of
the safety rules is not merged, however useful it is. Everyone taking part
follows the [code of conduct](CODE_OF_CONDUCT.md).

This guide covers:

- [The safety rules](#the-safety-rules)
- [How Statement Sweep works](#how-statement-sweep-works)
- [Repository layout](#repository-layout)
- [Setting up](#setting-up)
- [Adding a site](#adding-a-site), step by step
- [Testing](#testing)
- [Pull requests](#pull-requests)
- [Other policies](#other-policies): privacy, hosts, logs, dependencies,
  naming, security and the license

## The safety rules

1. **Read and download only.** A recipe never submits a form, changes a
   setting, accepts terms, or moves money. It presses only a document's own
   download or view control, or, for card offers, an offer's own Add control.
2. **The person signs in, never the code.** A recipe never types a password
   or a code, answers a security check, or works around a sign-in or CAPTCHA.
   It answers `signed_out` or `challenge` and waits.
3. **When unsure, stop.** If a page is not what the recipe expects, it answers
   `stopped` with a reason. It never guesses a date, an account or a button.
4. **Same-site only.** Requests go only to the hosts in the site's `match`, or
   its exact `fileHosts`. Nothing about an account is sent anywhere.
5. **Every new host needs a reason.** A change to `match` or `fileHosts`
   widens what the extension may read. Say in the pull request which page
   needs the host and why a narrower one will not do.
6. **Nothing personal in the repository.** Tests and recipes use invented
   values only: no real account or card numbers, names, addresses, amounts,
   parcel numbers or merchant lists, and never a page saved from your own
   account without redacting it (see [Stand-in pages](#stand-in-pages)).
   `tools/privacy_check.py` runs in CI and fails on any long number not
   listed in `tools/privacy-allow.txt`. If yours is invented, list it there.
7. **Log what you saw, not what it said.** A recipe's `__sweepLog` lines say
   which layout it found and why it stopped, in the recipe's own words, never
   the page's text, a cookie, a token or a document's contents.

## How Statement Sweep works

Statement Sweep is a Manifest V3 Chrome extension in plain JavaScript, with no
build step and no dependencies. It never signs in for anyone. It uses the
sign-in the person already has in their own Chrome, and reads each site from
inside that site's own page.

```
popup / Settings ──► background.js (the engine) ──► the site's tab
                          │                            │
                          │   chrome.scripting puts    ▼
                          │   shared.js + the recipe   providers/<id>.page.js
                          │   in the page, then calls  answers readList(),
                          │   readList() and a save    fetchPdf(), clickDoc() ...
                          ▼
                     chrome.downloads ──► Downloads/Statement Sweep/<site>/
```

For each site in a sync, the engine:

1. **Finds or opens a tab** on the site's `match` hosts. It reuses a tab of
   yours only if the recipe there already reads a list, and otherwise opens
   `listUrl`.
2. **Waits for the person** while the recipe answers `signed_out`,
   `challenge` or `waiting`, or while the tab is on a sign-in host outside
   `match`. It shows a note in the popup and a guide card in the tab
   (`guide.js`).
3. **Reads the list.** It calls the recipe's `readList()` every couple of
   seconds until the answer is `ok` or `stopped`. Two `no_list` answers in a
   row send a tab the sweep opened back to `listUrl`, once per wait.
4. **Saves what is new.** Every document whose `id` is not yet in the site's
   record (`seen:<id>` in `chrome.storage.local`) is saved by its `method`,
   named by `fileName` (`naming.js`), and added to the record.
5. **Writes a log** of what happened, with counts and reasons (`log.js`).

The pieces, all inside `extension/`:

| File | What it does |
|---|---|
| `background.js` | The engine: runs a sync, waits for sign-ins, calls recipes, saves files. |
| `providers/<id>.meta.json` | One site's settings: hosts, list address, file name, how it saves. |
| `providers/<id>.page.js` | One site's recipe, run inside the site's own page. |
| `providers/shared.js` | Loaded before every recipe: `__sweepPdf.pdfResult(bytes)` and `__sweepLog(text)`. |
| `providers/index.js` | The site list, made from the metas by `tools/build.py`. Never edited by hand. |
| `capture.js` | Saving for sites that open a PDF in a new tab, or need it printed. |
| `cadence.js` | Which sites are due, learned from the dates of what was saved. |
| `naming.js` | File and folder names, for the engine and the Settings preview. |
| `history.js` | Export and import of the record of what was saved. |
| `log.js` | The plain-text run logs. |
| `guide.js` | The card in a site's tab while the sweep waits for the person. |
| `progress.js` | The popup's progress bars. |
| `offers.js`, `providers/*.offers.js` | Card offers, off unless the person turns them on. |
| `sites.js`, `add-site.js`, `picker.js`, `providers/added.page.js` | Add This Site, for sites without a built-in recipe. |
| `popup.*`, `options.*`, `offers.html` | The popup, Settings and the Card Offers page. |

The full recipe contract, every meta key and the log format are in
[extension/providers/README.md](extension/providers/README.md). Read it before you write a recipe.

## Repository layout

```
extension/          the extension itself: what Chrome loads and the store gets
  manifest.json
  background.js     the engine, and the other scripts and pages beside it
  icons/
  providers/        one meta and one recipe per site, shared.js, index.js
tests/              pytest + Playwright, one file per recipe and per part
tools/              build.py, package.py, privacy_check.py, snapshot.py
docs/               images for the README
store/              the Chrome Web Store listing and screenshots
.github/            CI, Dependabot, issue forms, the pull request template
```

The license, notices, privacy policy and these guides stay at the top.
`tools/package.py` zips `extension/` with them for the Chrome Web Store.

## Setting up

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install chromium
```

Load the `extension` folder at `chrome://extensions` with Developer mode on
and **Load unpacked**. After changing a `*.meta.json` or any engine file, press the
extension's reload button there. A page refresh is not enough: Chrome keeps
the old background worker, and with it the old site list.

## Adding a site

First check whether you need a recipe at all. If the site lists each
statement as a link you can click, **Add This Site** in the popup handles it
with no code. A built-in recipe is for sites that show statements through a
viewer, a form, a date picker or a script, and for sites many people use.

### 1. Learn how the site really works

Sign in to your own account, open the statements page, and watch DevTools'
Network tab while the list loads and while you open one statement. You are
looking for:

- **The list:** a JSON or HTML answer that holds every document, each with
  the site's own id and a date.
- **The PDF:** the request that returns one document's bytes.
- **The sign-in:** which hosts and paths it uses, and what a signed-out or
  expired page looks like.

Then pick how documents are saved, in this order of preference:

| `method` | When | The recipe provides |
|---|---|---|
| `fetch` | The page can ask for the PDF itself, with the site's own cookies | `fetchPdf(doc)` |
| `click` | Only the site's own button starts a download | `clickDoc(doc)` |
| `newtab` | The button opens the PDF in a new tab or on a file host | `clickDoc(doc)`, plus `fileHosts` in the meta |
| `print` | The document is a web page, not a PDF | `showDoc`, `printReady`, optional `leaveDoc` |

`fetch` is the most reliable: it presses nothing, and a PDF is kept only if
its bytes start `%PDF-`. Keep notes on what you saw, never the values.

### 2. Write the meta

`extension/providers/<id>.meta.json`, with a short lowercase `id`:

```json
{
  "id": "examplebank",
  "name": "Example Bank",
  "match": ["https://secure.examplebank.com/*"],
  "listUrl": "https://secure.examplebank.com/statements",
  "pageFile": "providers/examplebank.page.js",
  "fileName": "{date} Example Bank {title}.pdf",
  "family": "fetch",
  "postsAfterDays": 3,
  "guide": { "signIn": ["Sign in to Example Bank in this tab."] }
}
```

`match` lists every host the list and the PDFs use, and no others.
`listUrl` is the page the person should land on, signed in. If the site signs
in through another company, the sign-in host stays out of `match`: the engine
waits while the tab is there.

Then run `python3 tools/build.py`. It checks the meta and rewrites
`extension/providers/index.js` and the manifest's `host_permissions`. Commit all
three.

### 3. Write the recipe

`extension/providers/<id>.page.js` runs inside the site's page, in the extension's
isolated world, and sets `globalThis.__sweep`. A starting point for a `fetch`
site:

```js
// Runs inside Example Bank's own page, where the sign-in already lives. It
// asks the site's own two addresses from the page, the list and each PDF,
// and presses nothing.
(() => {
  const HOST = "secure.examplebank.com";
  const LIST = "/api/statements";
  const KEY = /^[A-Za-z0-9-]{6,64}$/;         // the site's own id, never a path
  const signedOut = () => location.hostname !== HOST || Boolean(document.querySelector("input[type='password']"));

  async function readList() {
    if (signedOut()) return { state: "signed_out" };
    if (!location.pathname.startsWith("/statements")) return { state: "no_list" };
    let answer;
    try {
      const r = await fetch(LIST, { credentials: "same-origin", headers: { accept: "application/json" } });
      if (r.status === 401 || r.status === 403) return { state: "signed_out" };
      answer = await r.json();
    } catch (e) {
      return { state: "stopped", why: "Example Bank's list did not answer.", docs: [] };
    }
    if (!Array.isArray(answer.statements)) return { state: "stopped", why: "The list was not the shape this reads.", docs: [] };
    const docs = [];
    for (const s of answer.statements) {
      const date = /^\d{4}-\d{2}-\d{2}$/.test(s.date) ? s.date : null;
      if (!KEY.test(s.id || "") || !date) return { state: "stopped", why: "A statement could not be read.", docs };
      docs.push({ id: s.id, date, title: "Statement", method: "fetch" });
    }
    docs.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));  // newest first
    return { state: "ok", docs };
  }

  async function fetchPdf(doc) {
    if (!KEY.test((doc && doc.id) || "")) return { ok: false, why: "not an Example Bank statement" };
    if (signedOut()) return { ok: false, signedOut: true, why: "The tab is not signed in." };
    const r = await fetch(`${LIST}/${encodeURIComponent(doc.id)}/pdf`, { credentials: "same-origin" });
    if (r.status === 401 || r.status === 403) return { ok: false, signedOut: true, why: "Example Bank asked for a sign-in." };
    if (!r.ok) return { ok: false, why: `The site sent ${r.status}.` };
    return globalThis.__sweepPdf.pdfResult(new Uint8Array(await r.arrayBuffer()));  // kept only if it starts %PDF-
  }

  globalThis.__sweep = { readList, fetchPdf };
})();
```

What every recipe gets right:

- **A document's `id` never changes.** The record of what was saved is a list
  of ids, so a new id format saves every document again. Use the site's own
  id. If you must build one, build it from parts that never change.
- **`date` is the document's own date,** as `YYYY-MM-DD`: a statement's
  closing date, or the last day of its month. A tax form is dated the last
  day of its tax year, `YYYY-12-31`. Never the day it was downloaded.
- **Docs are newest first.** Two of the same kind on one day get a number in
  their `title` (`Statement 1`, `Statement 2`), so neither file is lost.
- **Everything taken from the page is checked** before it goes into an
  address: ids against a narrow pattern, dates for being real.
- **A list that is partly unreadable** answers `stopped` with the docs it
  could read, so they are still saved and the problem is still seen.
- **Each answer is the site's own.** A 401 or 403, or an answer that landed
  on a sign-in page or another host, is `signed_out`, not an error.
- **Ported code says so.** If the recipe follows PaperPull's, say which file
  in the header comment, as `paypal.page.js` does.

### 4. Write its tests

Every recipe has `tests/test_<id>_page.py`. It runs the recipe in a real
Chromium against stand-in pages, with every request answered by the test.
Start from a recipe like yours. `tests/test_paypal_page.py` is a short
`fetch` one. The shape:

```python
"""The Example Bank page logic, in a real Chromium, on stand-in pages.
Every id, date and amount here is invented."""
import json
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1] / "extension"
BASE = "https://secure.examplebank.com"
LIST = {"statements": [{"id": "ST-000002", "date": "2031-02-28"}, {"id": "ST-000001", "date": "2031-01-31"}]}


@pytest.fixture(scope="module")
def tab():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b.new_page()
        b.close()


def load(tab, path="/statements", body="<main>Statements</main>", answer=LIST):
    def respond(route):
        url = route.request.url
        if not url.startswith(BASE):
            route.abort()
        elif url == BASE + "/api/statements":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(answer))
        else:
            route.fulfill(status=200, content_type="text/html", body=f"<!doctype html>{body}")
    tab.unroute("**/*")
    tab.route("**/*", respond)
    tab.goto(BASE + path)
    tab.add_script_tag(content=(ROOT / "providers" / "shared.js").read_text())
    tab.add_script_tag(content=(ROOT / "providers" / "examplebank.page.js").read_text())


def test_the_list_is_read_newest_first(tab):
    load(tab)
    assert [d["id"] for d in tab.evaluate("__sweep.readList()")["docs"]] == ["ST-000002", "ST-000001"]


def test_a_password_field_is_signed_out(tab):
    load(tab, body="<input type=password>")
    assert tab.evaluate("__sweep.readList()") == {"state": "signed_out"}
```

Cover at least:

- the list, read newest first, with every field;
- signed out, by page and by a 401 or 403 answer;
- a list of another shape, and one bad row among good ones;
- an id that is not the site's shape asks for nothing;
- a PDF that comes back whole, and HTML sent as a PDF that is not kept.

Name tests as sentences that say what is true, as the others do.

### 5. Try it on the live site

Reload the extension, then press the play button beside the site in the
popup. Read its log in the site's `Logs` folder, or in Settings → Export
Logs. Try it signed out as well as signed in. Check the files: names, dates,
and that each opens.

### 6. Finish

- Add the site to the table and the count in [README.md](README.md#sites).
- Add a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md).
- Run the [checks](#before-you-open-a-pull-request) and open a pull request.

## Testing

```bash
python -m pytest -n auto --dist loadfile                 # everything, about two minutes
python -m pytest tests/test_paypal_page.py -q            # one recipe
python -m pytest tests/test_guards.py -k paypal -q       # one recipe's guard test
```

The suite has four kinds of test:

- **Recipe tests,** `tests/test_<id>_page.py`. One recipe, run in Chromium
  against stand-in pages.
- **The guard test,** `tests/test_guards.py`. Every enabled recipe is run
  on a page full of dangerous controls (pay, transfer, autopay, close,
  settings) and documents pointing at them. The test fails if a recipe
  presses one or asks for one. It runs on `listUrl`. A site whose `listUrl`
  only passes the tab on to another host says where its list is read, in
  `SIGNED_IN_PAGE`.
- **Engine tests:** `test_engine_guards.py`, `test_capture.py`,
  `test_cadence.py`, `test_naming.py`, `test_log.py` and the others test the
  engine's own rules.
- **Page tests:** `test_popup.py`, `test_offers_*.py`, `test_added_*.py`
  and the settings tests drive the popup and pages.

No test reaches the internet. `tests/conftest.py` makes every browser a test
starts find no host for anything the test does not answer itself. A typo'd
address fails the test instead of reaching a real site.

### Stand-in pages

Build stand-ins by hand from what the live page showed: its shape, never its
values. Use invented ids, dates in the 2030s, and amounts like `$80.00`. If
you need a whole page, `tools/snapshot.py` turns a page you saved into a
stand-in. It replaces digits, names, emails and amounts, and drops scripts
and tracking. Read the result before committing it: the tool helps, and you
are still the one who checks.

### Before you open a pull request

```bash
python3 tools/build.py          # after any change to a *.meta.json
python3 tools/privacy_check.py  # nothing personal
python -m pytest -n auto --dist loadfile
```

`privacy_check.py` reads only files git tracks, so `git add` new files before
you run it. CI runs these three checks, and also `node --check` on every
script. `main` accepts only pull requests whose checks pass.

## Pull requests

- **One change per pull request:** one site, or one fix. Branch from `main`.
- **Fill in the template.** Its safety checklist is the review.
- **Say how it was checked:** the tests, and whether you ran it on the live
  site, on which kind of account. Never paste a log's account lines, a
  statement, or a screenshot with real values.
- **A fix for a live site comes with a test** that fails without it, built
  from what the live page showed.
- **New hosts:** for each new host in `match` or `fileHosts`, say why.
- **Commits** say what changes for the person using it, in plain words.
  Commit with your GitHub noreply address if you would rather not publish
  your email.
- **Merging:** the maintainer squash-merges once checks pass.

## Other policies

### Privacy

Statement Sweep has no server, no account and no analytics, and a
contribution may not add one. Nothing a recipe reads leaves the browser,
except the requests the site's own page would make to its own hosts. What
the extension keeps, and where, is in [PRIVACY.md](PRIVACY.md). A change that
keeps something new, or keeps it somewhere new, updates that file in the same
pull request.

### Hosts and permissions

`host_permissions` is built only from the metas. A recipe may not ask for
another permission. Add This Site asks for one site at a time, only when the
person presses it. A new manifest permission needs its own pull request and
a reason.

### Logs

Engine and recipe log lines hold counts, states, layouts and reasons. The
engine masks long digit runs and drops address queries, but a recipe does
not rely on that: it never logs a value it read. The format is in
[extension/providers/README.md](extension/providers/README.md#logs).

### Card offers

An offers recipe may press only each offer's own add control, and runs only
for sites where the person turned offers on. The engine sets the pace and
the limit. The contract is at the top of `offers.js`.

### Dependencies

The extension has none, and keeps it that way: no npm, no bundler, no
library copied in. Test tools are pinned in `requirements-dev.txt`. GitHub
Actions are pinned to a commit, and Dependabot proposes updates.

### Naming and credit

The product is **Statement Sweep**. It is based on
[PaperPull](https://github.com/rheeloaded/paperpull), credited in
[NOTICE.md](NOTICE.md), and is not made or endorsed by it. Do not use the
PaperPull name in the extension's name, its pages or its store listing.

### Style

- Plain JavaScript modules, no build step and no dependencies.
- Comments say why, in plain words. Names say what.
- Keep user-facing text short and plain, in sentence case for notes and Title
  Case for buttons, as the popup and Settings do.

### Reporting a site that broke

Use the **A site broke** issue form. Attach the site's log from Settings →
Export Logs, which masks long numbers, and never a statement.

### Security

Report a security problem privately, as [SECURITY.md](SECURITY.md) says, not
in an issue.

### License

Statement Sweep is AGPL-3.0, and based on PaperPull (see
[NOTICE.md](NOTICE.md)). By contributing you agree your work is shared under
the same license.
