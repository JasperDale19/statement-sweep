"""The Chase Offers page logic (chase.offers.js), in a real Chromium, on
stand-in pages. Every merchant, offer, card and number is invented."""
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1] / "extension"
HUB = "https://secure.chase.com/web/auth/dashboard#/dashboard/merchantOffers/offer-hub?accountId=900000001234"
DASH = "https://secure.chase.com/web/auth/dashboard#/dashboard/overview"
PERSONAL = "SAPPHIRE PREFERRED (...1234)"
OTHER = "FREEDOM UNLIMITED (...5678)"
INK = "INK BUSINESS CASH (...9876)"


def tile(merchant, words, control="Add offer", added=False, label=None):
    inner = (f'<span role="img" aria-label="Success Added"></span>' if added
             else f'<button aria-label="{label or control}">+</button>')
    return f'<div data-cy="commerce-tile"><span>{merchant}</span><span>{words}</span><span>12 days left</span>{inner}</div>'


def switcher(on, others=()):
    if not on:
        return ""
    opts = "".join(f'<div role="option" hidden>{c}</div>' for c in [on, *others])
    return f'<button aria-haspopup="listbox" id="switch">{on}</button><div id="opts">{opts}</div>'


# Pressing an add control marks its tile added a moment later, as Chase does,
# or, with `window.detailOnAdd`, opens the offer's own page with a Back.
SCRIPT = """<script>
window.pressed = [];
document.addEventListener('click', (e) => {
  const el = e.target.closest('button, a, [role=option], [role=button], [role=tab]');
  if (!el) return;
  if (el.tagName === 'A') e.preventDefault();
  pressed.push((el.getAttribute('aria-label') || el.textContent).trim());
  const tile = el.closest('[data-cy=commerce-tile]');
  if (tile && el.tagName === 'BUTTON') {
    if (window.detailOnAdd) {
      window.saved = document.getElementById('list').innerHTML;
      location.hash = '#/dashboard/merchantOffers/offer-activated';
      document.getElementById('list').innerHTML = '<p>Added to card</p><button>Back to offers</button>';
      document.querySelector('#list button').onclick = () => {
        location.hash = '#/dashboard/merchantOffers/offer-hub?accountId=900000001234';
        document.getElementById('list').innerHTML = window.saved;
        document.querySelector('#list [data-cy=commerce-tile] button').outerHTML = '<span role="img" aria-label="Success Added"></span>';
      };
    } else {
      setTimeout(() => { el.outerHTML = '<span role="img" aria-label="Success Added"></span>'; }, 80);
    }
  }
  if (el.id === 'switch') document.querySelectorAll('#opts [role=option]').forEach((o) => { o.hidden = !o.hidden; });
  if (el.getAttribute('role') === 'option' && window.cards) {
    const label = el.textContent.trim();
    setTimeout(() => {
      document.getElementById('switch').textContent = label;
      document.querySelectorAll('#opts [role=option]').forEach((o) => { o.hidden = true; });
      document.getElementById('list').innerHTML = window.cards[label];
    }, 80);
  }
});
</script>"""


def page(body, card=PERSONAL, others=()):
    return f"<header><a href='#/dashboard/overview'>Home</a></header>{switcher(card, others)}<main id='list'>{body}</main>{SCRIPT}"


@pytest.fixture(scope="module")
def tab():
    with sync_playwright() as p:
        b = p.chromium.launch()
        t = b.new_page()
        yield t
        b.close()


def load(tab, url, body):
    tab.goto("about:blank")
    tab.unroute("**/*")
    tab.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=f"<!doctype html>{body}"))
    tab.goto(url)
    kit = (ROOT / "providers" / "offers-kit.js").read_text()
    source = (ROOT / "providers" / "chase.offers.js").read_text() \
        .replace("LIST_WAIT_MS = 15000", "LIST_WAIT_MS = 300").replace("ADD_WAIT_MS = 10000", "ADD_WAIT_MS = 800") \
        .replace("BACK_WAIT_MS = 10000", "BACK_WAIT_MS = 800").replace("CARD_WAIT_MS = 15000", "CARD_WAIT_MS = 800") \
        .replace("SWITCHER_WAIT_MS = 5000", "SWITCHER_WAIT_MS = 500")
    tab.add_script_tag(content=kit)
    tab.add_script_tag(content=source)


def read(tab, cards="personal", walk=True):
    return tab.evaluate("([w, c]) => __offers.readOffers({walk: w, cards: c})", [walk, cards])


def add(tab, offer):
    return tab.evaluate("(o) => __offers.addOne(o)", offer)


def test_offers_are_read_per_card(tab):
    load(tab, HUB, page(tile("Example Grocer", "10% cash back") + tile("Invented Hardware", "$15 cash back", added=True)))
    got = read(tab)
    assert got["state"] == "ok" and got["card"] == "1234" and got["more"] is True
    first, second = got["offers"]
    assert first["merchant"] == "Example Grocer" and first["title"] == "10% cash back" and first["added"] is False
    assert first["id"].startswith("1234:")
    assert second["merchant"] == "Invented Hardware" and second["added"] is True


def test_add_offer_named_with_its_merchant_counts(tab):
    load(tab, HUB, page(tile("Example Grocer", "10% cash back", label="Add offer, Example Grocer")))
    assert [o["added"] for o in read(tab)["offers"]] == [False]


def test_add_presses_only_that_tiles_add_control(tab):
    load(tab, HUB, page(tile("Example Grocer", "10% cash back") + tile("Invented Hardware", "$15 cash back")))
    offer = read(tab)["offers"][1]
    assert add(tab, offer) == {"ok": True}
    assert tab.evaluate("pressed") == ["Add offer"]
    assert tab.evaluate("document.querySelectorAll('[data-cy=commerce-tile] button').length") == 1


def test_controls_that_say_more_than_add_are_never_offers(tab):
    body = "".join(tile(f"Shop {i}", "5% cash back", control=c) for i, c in enumerate(
        ["Activate offer", "Add offer and accept terms", "Apply now", "Add authorized user", "Enroll", "Buy now", "Add to card and pay"]))
    load(tab, HUB, page(body))
    got = read(tab)
    assert got["state"] == "stopped"
    assert tab.evaluate("pressed") == []


def test_a_bare_plus_outside_a_tile_is_not_an_offer(tab):
    load(tab, HUB, page('<div><span>Example Grocer</span><button>+</button></div><p>No offers available.</p>'))
    got = read(tab)
    assert got["state"] == "ok" and got["offers"] == []


def test_an_offer_page_opened_by_the_press_is_left_by_its_own_back(tab):
    load(tab, HUB, page(tile("Example Grocer", "10% cash back")))
    offer = read(tab)["offers"][0]
    tab.evaluate("window.detailOnAdd = true")
    assert add(tab, offer) == {"ok": True}
    assert tab.evaluate("pressed") == ["Add offer", "Back to offers"]
    assert "offer-hub" in tab.evaluate("location.hash")


def test_a_business_card_is_passed_over_by_chase_personal(tab):
    load(tab, HUB, page(tile("Example Grocer", "10% cash back"), card=INK))
    got = read(tab, cards="personal")
    assert got["offers"] == [] and got["passed"] is True and got["more"] is True


def test_chase_business_reads_its_ink_card_and_passes_a_personal_one(tab):
    load(tab, HUB, page(tile("Example Grocer", "10% cash back"), card=INK))
    got = read(tab, cards="business")
    assert got["card"] == "9876" and len(got["offers"]) == 1
    load(tab, HUB, page(tile("Example Grocer", "10% cash back"), card=PERSONAL))
    assert read(tab, cards="business")["passed"] is True


def test_each_card_in_turn_through_the_switcher(tab):
    cards = {PERSONAL: tile("Example Grocer", "10% cash back"), OTHER: tile("Invented Hardware", "$15 cash back")}
    load(tab, HUB, page(cards[PERSONAL], PERSONAL, [OTHER]))
    tab.evaluate("(c) => { window.cards = c; }", cards)
    assert read(tab)["card"] == "1234"
    assert tab.evaluate("__offers.nextCard()") == {"moved": True}
    second = read(tab)
    assert second["card"] == "5678" and second["offers"][0]["merchant"] == "Invented Hardware"
    assert tab.evaluate("__offers.nextCard()") == {"moved": False}


def test_an_offer_for_another_card_is_not_pressed(tab):
    load(tab, HUB, page(tile("Example Grocer", "10% cash back")))
    offer = dict(read(tab)["offers"][0], card="5678")
    assert add(tab, offer)["ok"] is False
    assert tab.evaluate("pressed") == []


def test_off_the_hub_its_own_link_is_pressed_once_or_it_is_no_list(tab):
    load(tab, DASH, page("<a href='#/dashboard/merchantOffers/offer-hub'>Chase Offers</a>", card=""))
    assert read(tab, walk=False) == {"state": "no_list"}
    assert read(tab) == {"state": "walking"}
    assert tab.evaluate("pressed") == ["Chase Offers"]
    assert read(tab) == {"state": "no_list"}


def test_a_link_off_chase_is_not_pressed(tab):
    load(tab, DASH, "<a href='https://example.com/offers'>Chase Offers</a>" + SCRIPT)
    assert read(tab) == {"state": "no_list"}
    assert tab.evaluate("pressed") == []


def test_all_offers_and_more_are_pressed_before_reading(tab):
    body = '<button role="tab" aria-selected="false">All offers</button>' + tile("Example Grocer", "10% cash back") + "<button>Show more</button>"
    load(tab, HUB, page(body))
    assert read(tab) == {"state": "walking"}
    assert read(tab) == {"state": "walking"}
    assert tab.evaluate("pressed") == ["All offers", "Show more"]


def test_sign_in_frame_and_checks_are_known(tab):
    load(tab, HUB, "<iframe name='logonbox' src='about:blank' style='width:300px;height:300px'></iframe>")
    assert read(tab) == {"state": "signed_out"}
    load(tab, HUB, page("<p>Enter the code we sent to your phone.</p>"))
    assert read(tab) == {"state": "challenge"}


def test_a_dialog_after_the_press_is_closed_and_the_offer_left(tab):
    load(tab, HUB, page(tile("Example Grocer", "10% cash back")))
    offer = read(tab)["offers"][0]
    tab.evaluate("""document.querySelector('[data-cy=commerce-tile] button').addEventListener('click', (e) => {
      e.stopImmediatePropagation();
      const d = document.createElement('div'); d.setAttribute('role', 'dialog');
      d.innerHTML = '<p>Review the terms.</p><button>I agree</button><button>Close</button>';
      d.querySelector('button:last-child').onclick = () => d.remove();
      document.body.append(d);
    })""")
    got = add(tab, offer)
    assert got["ok"] is False and got["left"] is True
    assert tab.evaluate("pressed") == ["Close"]


def test_a_card_whose_kind_cannot_be_read_is_never_added_to(tab):
    # No switcher and no card name: it could be either kind.
    load(tab, HUB, page(tile("Example Grocer", "10% cash back"), card=""))
    for kind in ("personal", "business"):
        got = read(tab, cards=kind)
        assert got["state"] == "stopped" and "personal or business" in got["why"]
    assert tab.evaluate("pressed") == []


def test_each_offer_carries_its_kind_and_is_checked_again_before_the_press(tab):
    load(tab, HUB, page(tile("Example Grocer", "10% cash back"), card=PERSONAL))
    offer = read(tab, cards="personal")["offers"][0]
    assert offer["kind"] == "personal"
    # The same last digits on a business card on show: refused, and the offers stop.
    tab.evaluate("(l) => { document.getElementById('switch').textContent = l; }", "INK BUSINESS CASH (...1234)")
    got = add(tab, offer)
    assert got["ok"] is False and got["stop"] is True
    assert tab.evaluate("pressed") == []


def test_a_personal_and_a_business_card_in_one_sign_in_are_each_read_by_their_own_site(tab):
    cards = {PERSONAL: tile("Example Grocer", "10% cash back"), INK: tile("Invented Hardware", "$15 cash back")}
    for kind, want in (("personal", ["1234"]), ("business", ["9876"])):
        load(tab, HUB, page(cards[PERSONAL], PERSONAL, [INK]))
        tab.evaluate("(c) => { window.cards = c; }", cards)
        added_to = []
        for _ in range(3):
            got = read(tab, cards=kind)
            if got["offers"]:
                added_to.append(got["card"])
                assert all(o["kind"] == kind for o in got["offers"])
            if tab.evaluate("__offers.nextCard()") == {"moved": False}:
                break
        assert added_to == want


def test_a_check_during_an_add_presses_nothing(tab):
    load(tab, HUB, page(tile("Example Grocer", "10% cash back")))
    offer = read(tab)["offers"][0]
    tab.evaluate("document.body.insertAdjacentHTML('afterbegin', '<p>Enter the code we sent to your phone.</p>')")
    assert add(tab, offer) == {"ok": False, "state": "challenge"}
    assert tab.evaluate("pressed") == []


def test_a_page_asking_for_a_pause_stops_the_offers_without_a_press(tab):
    load(tab, HUB, page(tile("Example Grocer", "10% cash back")))
    offer = read(tab)["offers"][0]
    tab.evaluate("document.body.insertAdjacentHTML('afterbegin', '<p>Too many requests. Try again later.</p>')")
    got = add(tab, offer)
    assert got["ok"] is False and got["stop"] is True
    assert tab.evaluate("pressed") == []


def test_days_left_are_read_as_an_end_date_counted_from_today(tab):
    load(tab, HUB, page(tile("Example Grocer", "10% cash back")))
    got = read(tab)
    want = tab.evaluate("(() => { const d = new Date(); d.setDate(d.getDate() + 12); return [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'), String(d.getDate()).padStart(2, '0')].join('-'); })()")
    assert got["offers"][0]["expires"] == want


def test_the_cards_product_name_is_read_but_never_a_cardholders(tab):
    load(tab, HUB, page(tile("Example Grocer", "10% cash back")))
    assert read(tab)["cardName"] == "SAPPHIRE PREFERRED"
    load(tab, HUB, page(tile("Example Grocer", "10% cash back"), card="J. INVENTED (...4321)"))
    got = read(tab, cards="business")
    assert got["state"] == "ok" and got["card"] == "4321" and "cardName" not in got


# Chase Offers since 2026-11: the whole tile is the offer's one control, its
# own aria-label ending in what pressing it does ("Add Offer", "Success
# Added"), its aria-labelledby naming only its banner. With one card there is
# no switcher, only a read-only box saying "Sapphire Preferred...1234".
def whole_tile(n, merchant, words, added=False, banner=""):
    end = "Success Added" if added else "Add Offer"
    tag = f'<span data-testid="tile-banner"><span>{banner}</span></span>' if banner else ""
    by = f' aria-labelledby="banner-{n}"' if banner else ""
    return (f'<a href="/#" role="button" data-cy="commerce-tile" data-testid="commerce-tile"{by} '
            f'aria-label="{n} of 9 {merchant} {words} {end}"><div>'
            f'<div role="img" id="banner-{n}">{tag}</div>'
            f'<div><span>{merchant}</span><span>{words}</span></div>'
            f'<svg aria-hidden="true" width="16" height="16"><path d="M0 0h16v16H0z"/></svg></div></a>')


WHOLE_SCRIPT = """<script>
window.pressed = [];
document.addEventListener('click', (e) => {
  const el = e.target.closest('a, button');
  if (!el) return;
  e.preventDefault();
  pressed.push(el.getAttribute('aria-label') || el.textContent.trim());
  const label = el.getAttribute('aria-label') || '';
  if (el.matches('[data-cy=commerce-tile]') && / Add Offer$/.test(label))
    setTimeout(() => el.setAttribute('aria-label', label.replace(/ Add Offer$/, ' Success Added')), 80);
});
</script>"""


def whole_page(*tiles, card="Sapphire Preferred...1234"):
    box = f'<div>Choose account <div data-testid="account-selector-readonly"><span>{card}</span></div></div>' if card else ""
    return f"{box}<main id='list'>{''.join(tiles)}</main>{WHOLE_SCRIPT}"


def test_whole_tiles_are_read_by_their_own_label(tab):
    load(tab, HUB, whole_page(whole_tile(1, "Example Grocer", "10% cash back", banner="New"),
                              whole_tile(2, "Invented Hardware", "$15 cash back", added=True, banner="Exclusive"),
                              whole_tile(3, "Example Books", "5% cash back")))
    got = read(tab)
    assert got["state"] == "ok" and got["card"] == "1234" and got["cardName"] == "Sapphire Preferred"
    assert [(o["merchant"], o["title"], o["added"]) for o in got["offers"]] == [
        ("Example Grocer", "10% cash back", False), ("Invented Hardware", "$15 cash back", True), ("Example Books", "5% cash back", False)]
    assert tab.evaluate("pressed") == []


def test_a_whole_tile_add_presses_only_that_tile_and_sees_it_added(tab):
    load(tab, HUB, whole_page(whole_tile(1, "Example Grocer", "10% cash back"), whole_tile(2, "Example Books", "5% cash back")))
    offer = read(tab)["offers"][1]
    assert add(tab, offer) == {"ok": True}
    assert tab.evaluate("pressed") == ["2 of 9 Example Books 5% cash back Add Offer"]
    assert [o["added"] for o in read(tab)["offers"]] == [False, True]


def test_a_whole_tile_saying_anything_else_is_not_an_offer_control(tab):
    odd = whole_tile(1, "Example Grocer", "10% cash back").replace(" Add Offer", " Activate and Accept Terms")
    load(tab, HUB, whole_page(odd))
    got = read(tab)
    assert got["state"] == "stopped" and tab.evaluate("pressed") == []


def test_a_read_only_business_card_is_passed_over(tab):
    load(tab, HUB, whole_page(whole_tile(1, "Example Grocer", "10% cash back"), card="Ink Business Cash...9876"))
    assert read(tab, cards="personal") == {"state": "ok", "card": "9876", "offers": [], "passed": True}
    assert tab.evaluate("pressed") == []


# The hub's All filter opens every offer in one list, which draws no card,
# only the accountId in its address. A carousel's own "See more offers" opens
# just that carousel's collection.
ALL_SCRIPT = """<script>
document.addEventListener('click', (e) => {
  const el = e.target.closest('button');
  if (!el) return;
  if (el.dataset.testid === 'carouselSeeAllOrSeeMoreButton') window.carouselPressed = true;
  if (el.dataset.testid === 'offerHubCategoryNavbarButtonTopLink') setTimeout(() => {
    location.hash = '#/dashboard/merchantOffers/offerCategoriesPage?accountId=' + (window.nextAccount || '900000001234') + '&offerCategoryName=ALL';
    document.getElementById('box').remove();
    document.getElementById('list').innerHTML = window.allList;
  }, 50);
});
</script>"""


def hub_with_all(carousel, everything, next_account=None):
    box = '<div id="box">Choose account <div data-testid="account-selector-readonly"><span>Sapphire Preferred...1234</span></div></div>'
    nav = ('<button data-testid="offerHubCategoryNavbarButtonTopLink" aria-label="New 1 of 8">New</button>'
           '<button data-testid="offerHubCategoryNavbarButtonTopLink" aria-label="All 2 of 8">All</button>')
    more = '<button data-testid="carouselSeeAllOrSeeMoreButton">See more offers</button>'
    setup = f"<script>window.allList = {everything!r}; window.nextAccount = {next_account!r};</script>".replace("None", "null")
    return f"{box}{nav}<main id='list'>{carousel}{more}</main>{WHOLE_SCRIPT}{ALL_SCRIPT}{setup}"


def walk_to_end(tab, cards="personal"):
    for _ in range(6):
        got = read(tab, cards=cards)
        if got["state"] != "walking":
            return got
        tab.wait_for_timeout(150)
    return got


def test_the_hub_walks_to_all_and_keeps_the_card_read_there(tab):
    carousel = whole_tile(1, "Example Grocer", "10% cash back")
    everything = whole_tile(1, "Example Grocer", "10% cash back") + whole_tile(2, "Example Books", "5% cash back", added=True) \
        + whole_tile(3, "Invented Hardware", "$15 cash back")
    load(tab, HUB, hub_with_all(carousel, everything))
    got = walk_to_end(tab)
    assert "offerCategoryName=ALL" in tab.evaluate("location.hash")
    assert got["state"] == "ok" and got["card"] == "1234" and got["cardName"] == "Sapphire Preferred"
    assert [(o["merchant"], o["added"]) for o in got["offers"]] == [("Example Grocer", False), ("Example Books", True), ("Invented Hardware", False)]
    assert tab.evaluate("window.carouselPressed") is None
    offer = got["offers"][2]
    assert add(tab, offer) == {"ok": True}
    assert tab.evaluate("pressed")[-1] == "3 of 9 Invented Hardware $15 cash back Add Offer"


def test_a_card_kept_for_one_account_does_not_stand_for_another(tab):
    load(tab, HUB, hub_with_all(whole_tile(1, "Example Grocer", "10% cash back"),
                                whole_tile(1, "Example Grocer", "10% cash back"), next_account="900000001"))
    got = walk_to_end(tab)
    assert got["state"] == "stopped" and "card on show could not be read" in got["why"]
    assert tab.evaluate("pressed") == ["All 2 of 8"]


def test_a_card_drawn_after_the_offers_is_waited_for_before_all(tab):
    load(tab, HUB, hub_with_all(whole_tile(1, "Example Grocer", "10% cash back"),
                                whole_tile(1, "Example Grocer", "10% cash back") + whole_tile(2, "Example Books", "5% cash back")))
    # The hub's card box arrives a moment after its offers, as on the real site.
    tab.evaluate("""() => { const box = document.getElementById('box'); const keep = box.innerHTML; box.innerHTML = '';
                            setTimeout(() => { box.innerHTML = keep; }, 300); }""")
    got = walk_to_end(tab)
    assert got["state"] == "ok" and got["card"] == "1234" and len(got["offers"]) == 2


# Pressing an offer in the All list adds it and opens its own Offer Details
# page, whose address is under the hub's and which shows a few other offers.
DETAIL_SCRIPT = """<script>
document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-cy=commerce-tile]');
  if (!el) return;
  e.preventDefault();
  if (!/ Add Offer$/.test(el.getAttribute('aria-label') || '')) return;
  setTimeout(() => {
    window.savedList = document.getElementById('list').innerHTML.replace(el.outerHTML,
      el.outerHTML.replace(' Add Offer"', ' Success Added"'));
    history.pushState(null, '', '#/dashboard/merchantOffers/offer-activated/FIGG:1?accountId=900000001234&offerCategoryName=ALL');
    document.getElementById('list').innerHTML = '<p>Success! This offer is added to your card.</p>' + window.otherOffers;
  }, 50);
}, true);
window.addEventListener('popstate', () => { document.getElementById('list').innerHTML = window.savedList; });
</script>"""


def test_after_each_add_the_offer_details_page_is_left_for_the_list(tab):
    listing = whole_tile(1, "Example Grocer", "10% cash back") + whole_tile(2, "Example Books", "5% cash back")
    others = whole_tile(1, "Invented Shoes", "8% cash back") + whole_tile(2, "Example Tea", "3% cash back")
    all_url = HUB.replace("offer-hub?", "offerCategoriesPage?") + "&offerCategoryName=ALL"
    body = (f"<main id='list'>{listing}</main><script>window.otherOffers = {others!r};</script>"
            "<script>window.pressed = [];</script>" + DETAIL_SCRIPT)
    load(tab, all_url, body)
    tab.evaluate("window.__chaseOfferCardOf = {'900000001234': 'Sapphire Preferred (...1234)'}")
    offers = read(tab)["offers"]
    first = add(tab, offers[0])
    assert first["ok"] is True, first
    assert "offerCategoriesPage" in tab.evaluate("location.hash")
    second = add(tab, offers[1])
    assert second["ok"] is True
    assert "offerCategoriesPage" in tab.evaluate("location.hash")
    assert [o["added"] for o in read(tab)["offers"]] == [True, True]
