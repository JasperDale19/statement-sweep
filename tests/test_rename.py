"""Renaming what is already saved in the chosen folder to the file name
settings of now, in a real Chromium with the extension's own files. A file is
copied under its new name, checked, listed in a new entry, and only then is
the old one removed; an entry is never changed. The folder is a stand-in in
the browser's own storage. Every id, date and name is invented."""
import json
from test_shared_history import PDF_A, PDF_B, PDF_C, browser, entry, facts, run, tab, with_pdfs  # noqa: F401

OLD_A = "Statement Sweep/American Express/2026-10-28 American Express Monthly Statement Blue (76008).pdf"
OLD_B = "Statement Sweep/American Express/2026-09-28 American Express Monthly Statement Blue (76008).pdf"
NEW_A = "Statement Sweep/American Express/American Express 2026-10-28 Monthly Statement Blue (76008).pdf"
NEW_B = "Statement Sweep/American Express/American Express 2026-09-28 Monthly Statement Blue (76008).pdf"
NAMING = {"base": "Statement Sweep", "folder": "{provider}", "file": "{provider} {date}[ {title}]", "providers": {}}
AMEX = {"id": "amex", "name": "American Express", "fileName": "{date} American Express {title}.pdf"}
SDGE = {"id": "sdge", "name": "SDG&E", "fileName": "{date} SDG&E Utility Bill.pdf"}

OWN = "async (provider, leaf) => (await import('/naming.js')).fromOwnName(provider, leaf)"


def own(tab, provider, leaf):
    return run(tab, f"async () => ({OWN})({json.dumps(provider)}, {json.dumps(leaf)})")["result"]


def test_a_sites_own_file_name_gives_back_its_date_and_title(tab):
    assert own(tab, AMEX, "2026-10-28 American Express Monthly Statement Blue (76008).pdf") == {"date": "2026-10-28", "title": "Monthly Statement Blue (76008)"}


def test_a_name_with_no_title_in_it_has_a_blank_title_numbered_or_not(tab):
    assert own(tab, SDGE, "2026-05-28 SDG&E Utility Bill.pdf") == {"date": "2026-05-28", "title": ""}
    assert own(tab, SDGE, "2026-05-28 SDG&E Utility Bill (1).pdf") == {"date": "2026-05-28", "title": ""}


def test_a_name_the_site_did_not_make_gives_nothing(tab):
    assert own(tab, SDGE, "SDG&E 2026-05-28.pdf") is None
    assert own(tab, AMEX, "notes.pdf") is None


# The folder read, then what would be renamed under NAMING.
PLAN = f"""async (s, root) => {{
  const r = await s.reconcile(root);
  return s.renamePlan(r.record, [{json.dumps(AMEX)}, {json.dumps(SDGE)}], {json.dumps(NAMING)});
}}"""
TWO = entry({"amex": {"o1": facts(OLD_A, PDF_A), "o2": facts(OLD_B, PDF_B)}}, sites={"amex": ["o1", "o2"]},
            dates={"amex": {"o1": "2026-10-28", "o2": "2026-09-28"}})


def test_the_plan_names_each_file_by_the_settings_of_now(tab):
    got = run(tab, with_pdfs({OLD_A: PDF_A, OLD_B: PDF_B}, PLAN), {}, {"x.json": TWO})["result"]
    assert got["alone"] == 0
    assert sorted((m["id"], m["from"], m["to"], m["title"]) for m in got["moves"]) == [
        ("o1", OLD_A, NEW_A, "Monthly Statement Blue (76008)"), ("o2", OLD_B, NEW_B, "Monthly Statement Blue (76008)")]


def test_a_file_already_named_right_is_not_in_the_plan(tab):
    text = entry({"amex": {"o1": {**facts(NEW_A, PDF_A), "title": "Monthly Statement Blue (76008)"}}})
    got = run(tab, with_pdfs({NEW_A: PDF_A}, PLAN), {}, {"x.json": text})["result"]
    assert got == {"moves": [], "alone": 0}


def test_a_title_kept_in_an_entry_is_used_before_the_file_name(tab):
    odd = "Statement Sweep/American Express/whatever.pdf"
    text = entry({"amex": {"o1": {**facts(odd, PDF_A), "title": "Monthly Statement Blue (76008)"}}})
    got = run(tab, with_pdfs({odd: PDF_A}, PLAN), {}, {"x.json": text})["result"]
    assert [(m["from"], m["to"]) for m in got["moves"]] == [(odd, NEW_A)]


def test_a_file_whose_title_cannot_be_known_is_left_alone(tab):
    odd = "Statement Sweep/American Express/whatever.pdf"
    got = run(tab, with_pdfs({odd: PDF_A}, PLAN), {}, {"x.json": entry({"amex": {"o1": facts(odd, PDF_A)}})})["result"]
    assert got == {"moves": [], "alone": 1}


# The folder read, the plan carried out, then every PDF in the folder and what a new read finds.
RENAME = f"""async (s, root) => {{
  const before = await s.reconcile(root);
  const plan = s.renamePlan(before.record, [{json.dumps(AMEX)}, {json.dumps(SDGE)}], {json.dumps(NAMING)});
  const done = [];
  for (const site of [...new Set(plan.moves.map((m) => m.site))]) {{
    done.push(await s.renameSite(root, before, site, plan.moves.filter((m) => m.site === site), {{ now: new Date(2026, 10, 25, 9, 0, 0) }}));
  }}
  const pdfs = [];
  async function walk(d, p) {{ for await (const [n, h] of d.entries()) {{ if (h.kind === 'directory') await walk(h, p + n + '/'); else if (n.endsWith('.pdf')) pdfs.push(p + n); }} }}
  await walk(root, '');
  const after = await s.reconcile(root);
  return {{ done, pdfs: pdfs.sort(), record: after.record, planAgain: s.renamePlan(after.record, [{json.dumps(AMEX)}, {json.dumps(SDGE)}], {json.dumps(NAMING)}) }};
}}"""


def test_files_are_renamed_listed_in_a_new_entry_and_found_again(tab):
    got = run(tab, with_pdfs({OLD_A: PDF_A, OLD_B: PDF_B}, RENAME), {}, {"x.json": TWO})
    r = got["result"]
    assert r["pdfs"] == sorted([NEW_A, NEW_B])
    assert [d["renamed"] for d in r["done"]] == [2]
    assert r["record"]["amex"]["paths"] == {"o1": NEW_A, "o2": NEW_B}
    assert r["record"]["amex"]["dates"] == {"o1": "2026-10-28", "o2": "2026-09-28"}
    assert r["planAgain"] == {"moves": [], "alone": 0}
    # The first entry is as it was; one new entry lists the new places, and the titles.
    assert got["files"]["x.json"] == TWO and len(got["files"]) == 2
    new = json.loads(next(v for k, v in got["files"].items() if k != "x.json"))
    assert new["files"]["amex"]["o1"] == {**facts(NEW_A, PDF_A), "title": "Monthly Statement Blue (76008)"}


def test_a_copy_already_under_the_new_name_is_kept_and_the_old_one_removed(tab):
    got = run(tab, with_pdfs({OLD_A: PDF_A, NEW_A: PDF_A}, RENAME), {}, {"x.json": entry({"amex": {"o1": facts(OLD_A, PDF_A)}})})["result"]
    assert got["pdfs"] == [NEW_A] and got["record"]["amex"]["paths"] == {"o1": NEW_A}


def test_another_document_under_the_new_name_is_not_written_over(tab):
    got = run(tab, with_pdfs({OLD_A: PDF_A, NEW_A: PDF_C}, RENAME), {}, {"x.json": entry({"amex": {"o1": facts(OLD_A, PDF_A)}})})["result"]
    numbered = NEW_A.replace(".pdf", " (1).pdf")
    assert got["pdfs"] == sorted([NEW_A, numbered]) and got["record"]["amex"]["paths"] == {"o1": numbered}
    assert got["planAgain"] == {"moves": [], "alone": 0}


def test_a_file_changed_since_the_folder_was_read_is_left_where_it_is(tab):
    call = f"""async (s, root) => {{
      const before = await s.reconcile(root);
      const plan = s.renamePlan(before.record, [{json.dumps(AMEX)}], {json.dumps(NAMING)});
      const dir = await (await root.getDirectoryHandle('Statement Sweep')).getDirectoryHandle('American Express');
      const w = await (await dir.getFileHandle({json.dumps(OLD_A.split('/')[-1])})).createWritable(); await w.write('changed since'); await w.close();
      const done = await s.renameSite(root, before, 'amex', plan.moves, {{}});
      const names = []; for await (const [n] of dir.entries()) names.push(n);
      return {{ done, names }};
    }}"""
    got = run(tab, with_pdfs({OLD_A: PDF_A}, call), {}, {"x.json": entry({"amex": {"o1": facts(OLD_A, PDF_A)}})})
    assert got["result"]["done"]["renamed"] == 0 and got["result"]["names"] == [OLD_A.split("/")[-1]]
    assert len(got["files"]) == 1
