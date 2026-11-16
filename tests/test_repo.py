"""The repository agrees with itself: the issue form names every site, and
the README's count is the number switched on, so neither is left behind when
a site is added."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METAS = [json.loads(p.read_text()) for p in sorted((ROOT / "extension" / "providers").glob("*.meta.json"))]


def test_the_site_broke_form_lists_every_site_and_other():
    form = (ROOT / ".github" / "ISSUE_TEMPLATE" / "site-broke.yml").read_text()
    options = json.loads(re.search(r"options: (\[.*\])", form).group(1))
    assert options[-1] == "Other"
    assert sorted(options[:-1], key=str.lower) == options[:-1], "kept in order"
    assert set(options[:-1]) == {m["name"] for m in METAS}


def test_the_readme_counts_the_sites_switched_on():
    on = sum(m.get("enabled", True) for m in METAS)
    assert f"{on} sites are switched on." in (ROOT / "README.md").read_text()
