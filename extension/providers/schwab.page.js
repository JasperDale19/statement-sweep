// Runs inside Schwab's client site, where the sign-in already lives. It
// presses nothing and changes nothing: it reads the same document list the
// page itself reads, and asks for each PDF the way the page's own Download
// does.
//
// Derived from PaperPull's apps/schwab/schwab_site.py (AGPL-3.0).
//
// Schwab's accounts, document list and PDFs come from its gateway,
// ausgateway.schwab.com, with a bearer token the client site hands its own
// pages at /api/auth/authorize/scope/api. Both hosts are in this recipe's
// match. The page's own row buttons are not pressed: PaperPull stays off them
// on purpose, since the page shares its shell with a trade ticket.
(() => {
  const HOST = "client.schwab.com";
  const GATEWAY_HOST = "ausgateway.schwab.com";
  const GATEWAY = `https://${GATEWAY_HOST}/api/is.StatementsWeb/StatementsInterface/Statements`;
  const TOKEN_PATH = "/api/auth/authorize/scope/api";
  const GATEWAY_HEADERS = {
    accept: "application/json",
    "schwab-client-appid": "AD00008376",
    "schwab-resource-version": "1",
    "schwab-client-channel": "IO",
  };
  const DOCUMENT_TYPES = ["STATEMENTS", "TAXFORMS", "LETTERS", "REPORTS_AND_PLANNING", "CONFIRMS"];
  const LOOKBACK_YEARS = 10;
  const TOKEN_TTL_MS = 5 * 60 * 1000;

  const LOGIN_URL_MARKERS = ["/areas/access/login", "/areas/login/", "sessiontimeout=",
    "/login", "/logon", "/signin", "/sign-in", "/mfa", "www.schwab.com/"];
  const CHALLENGE_MARKERS = [
    "enter the code we sent", "enter your verification code", "verification code",
    "one-time", "one time passcode", "security code", "we sent a code",
    "two-factor", "two-step", "authenticator", "confirm your identity",
    "verify your identity", "we need to verify", "unusual activity",
    "are you a robot", "captcha", "unable to verify", "trouble verifying",
    "your session has expired", "please log in again", "you've been logged out",
    "for your security, we signed you out", "for your security we've signed",
  ];
  const RATE_LIMITS = [
    /too many requests/i,
    /rate limit(ed|ing)?\b/i,
    /unusual traffic/i,
    /\b(http\s*)?(error\s*)?429\b/i,
    /(site|service|page|system|application)\s+(is\s+)?(currently\s+|temporarily\s+)*unavailable/i,
    /we'?re\s+(currently\s+)?(experiencing|having)\s+(technical\s+)?(difficulties|issues)/i,
  ];
  const WAIT_MS = 10000;
  const log = (text) => globalThis.__sweepLog && globalThis.__sweepLog(text);

  function signedOut() {
    const url = location.href.toLowerCase();
    return LOGIN_URL_MARKERS.some((m) => url.includes(m))
      || Boolean(document.querySelector("input[type='password']"));
  }

  // Only the page's own path counts, never an address named in its query, as
  // a sign-in page's return address is.
  function onStatementsPath() {
    const path = location.pathname.toLowerCase();
    return location.protocol === "https:" && location.hostname === HOST
      && (path.includes("/app/accounts/statements") || path.includes("/retail/accounts/statements"));
  }

  const titled = () => document.title.toLowerCase().includes("statements");

  // Only Schwab's gateway, on https and the default port.
  function gatewayUrl(href) {
    let url;
    try { url = new URL(href, location.href); } catch (e) { return null; }
    if (url.protocol !== "https:" || url.hostname !== GATEWAY_HOST
        || url.username || url.password || (url.port && url.port !== "443")) return null;
    return url;
  }

  // The bearer token the client site gives its own pages. Kept only in this
  // page, never logged.
  let token = { value: "", at: 0 };
  async function getToken(force) {
    if (!force && token.value && Date.now() - token.at < TOKEN_TTL_MS) return token.value;
    token = { value: "", at: 0 };
    if (location.hostname !== HOST) return "";
    let response;
    try {
      response = await fetch(TOKEN_PATH, { redirect: "error", credentials: "same-origin",
        headers: { Accept: "application/json" } });
    } catch (e) {
      log("token: could not be asked for");
      return "";
    }
    let body = null;
    try { body = await response.json(); } catch (e) { /* not JSON: no token */ }
    const value = (body && typeof body.token === "string" && body.token) || "";
    if (!value) log(`token: answered ${response.status} with none`);
    else token = { value, at: Date.now() };
    return value;
  }

  // One gateway answer, as {status, response}, asked again once with a fresh
  // token when the first was refused. noToken when no token came at all.
  async function gateway(href, extra, post) {
    const url = gatewayUrl(href);
    if (!url) return { status: 0 };
    for (const force of [false, true]) {
      const bearer = await getToken(force);
      if (!bearer) return { status: 0, noToken: true };
      const opts = { redirect: "error", headers: { ...GATEWAY_HEADERS, ...extra, authorization: `Bearer ${bearer}` } };
      if (post) {
        opts.method = "POST";
        opts.headers["content-type"] = "application/json";
        opts.body = JSON.stringify(post);
      }
      let response;
      try {
        response = await fetch(url.href, opts);
      } catch (e) {
        return { status: 0 };
      }
      if ((response.status === 401 || response.status === 403) && !force) continue;
      return { status: response.status, response };
    }
    return { status: 0 };
  }

  async function gatewayJson(href, extra) {
    const got = await gateway(href, extra);
    if (!got.response) return got;
    let body = null;
    try { body = await got.response.json(); } catch (e) { /* not JSON */ }
    return { status: got.status, body };
  }

  // Every account of this sign-in, or a reason the list cannot be trusted.
  async function listAccounts() {
    const got = await gatewayJson(`${GATEWAY}/accounts`, {});
    if (got.noToken) return { signedOut: true };
    const list = got.body && got.body.accountSelectorData && got.body.accountSelectorData.brokerageAccountList;
    if (got.status !== 200 || !list || typeof list !== "object") return { why: `Schwab's accounts answer came back as ${got.status}.` };
    const rows = [];
    for (const key of ["brokerageAccounts", "viewOnlyAccounts"]) {
      if (!list[key]) continue;
      if (!Array.isArray(list[key])) return { why: "Schwab's accounts answer could not be read." };
      rows.push(...list[key]);
    }
    if (!rows.length) return { why: "Schwab's accounts answer held no account." };
    const accounts = [];
    const seen = new Set();
    for (const a of rows) {
      const id = a && a.id ? String(a.id) : "";
      if (!id) return { why: "An account in Schwab's answer had no number." };
      if (seen.has(id)) continue;
      seen.add(id);
      accounts.push({ id, charitable: Boolean(a.isCharitable) });
    }
    return { accounts };
  }

  const mdy = (d) => `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}/${d.getFullYear()}`;

  function documentsUrl() {
    const to = new Date();
    const from = new Date(to);
    from.setFullYear(to.getFullYear() - LOOKBACK_YEARS);
    const types = DOCUMENT_TYPES.map((t) => `documentTypes=${t}`).join("&");
    return `${GATEWAY}/brokerage/documents?${types}&timeFrame=Last10Years`
      + `&fromDate=${mdy(from)}&toDate=${mdy(to)}&isNonAllianceAccountAvailable=true`;
  }

  // "MM/DD/YYYY" or "YYYY-MM-DD..." as YYYY-MM-DD, only for a day that exists.
  function isoDate(text) {
    const s = String(text || "");
    let y, m, d;
    let hit = s.match(/\b(\d{1,2})\/(\d{1,2})\/(\d{4})\b/);
    if (hit) [y, m, d] = [+hit[3], +hit[1], +hit[2]];
    else if ((hit = s.match(/\b(\d{4})-(\d{2})-(\d{2})(?!\d)/))) [y, m, d] = [+hit[1], +hit[2], +hit[3]];
    else return "";
    const day = new Date(Date.UTC(y, m - 1, d));
    if (day.getUTCFullYear() !== y || day.getUTCMonth() !== m - 1 || day.getUTCDate() !== d) return "";
    return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  }

  const clean = (text) => String(text || "").replace(/\s+/g, " ").trim();
  const last4 = (display) => String(display || "").replace(/\D/g, "").slice(-4);

  // The id this document keeps from run to run and from Mac to Mac, which is
  // what the sweep's history remembers. Schwab's own documentId is not it:
  // PaperPull found it can change between two lists of the same document.
  // row is {type, name, date: "YYYY-MM-DD", last4}; `occurrence` counts
  // earlier documents of this list with the same four, starting at 0.
  function docKey(row, occurrence) {
    const name = row.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    const type = row.type.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    return ["schwab", row.date, type, name, row.last4 || "none", occurrence].join(":");
  }

  async function readDocs() {
    const found = await listAccounts();
    if (found.signedOut) return { state: "signed_out" };
    if (found.why) return { state: "stopped", why: found.why, docs: [] };
    const regular = found.accounts.filter((a) => !a.charitable).map((a) => a.id).join(",");
    const charitable = found.accounts.filter((a) => a.charitable).map((a) => a.id).join(",");
    const ids = {};
    if (regular) ids["schwab-client-ids"] = regular;
    if (charitable) ids["schwab-client-charitable-ids"] = charitable;
    const got = await gatewayJson(documentsUrl(), ids);
    if (got.noToken) return { state: "signed_out" };
    if (got.status !== 200 || !got.body || typeof got.body !== "object") {
      return { state: "stopped", why: `Schwab's document list came back as ${got.status}.`, docs: [] };
    }
    const list = got.body.documents;
    if (list != null && !Array.isArray(list)) return { state: "stopped", why: "Schwab's document list could not be read.", docs: [] };
    const partial = got.body.nonCharitableSuccess === false || got.body.charitableSuccess === false;

    const docs = [];
    const counts = new Map();
    let undated = 0;
    for (const raw of list || []) {
      if (!raw || typeof raw !== "object" || !raw.documentId) continue;
      const row = {
        type: clean(raw.type), name: clean(raw.documentName),
        date: isoDate(raw.date), last4: last4(raw.accountDisplayName),
      };
      if (!row.date) { undated += 1; continue; }
      const same = [row.type, row.name, row.date, row.last4].join("|");
      const occurrence = counts.get(same) || 0;
      counts.set(same, occurrence + 1);
      docs.push({
        id: docKey(row, occurrence),
        date: row.date,
        title: [row.name || row.type || "Document", row.last4 && `...${row.last4}`].filter(Boolean).join(" "),
        method: "fetch",
        documentId: String(raw.documentId),
        documentName: raw.documentName || "",
        documentType: raw.onDemandDocumentType || raw.type || "",
      });
    }
    docs.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
    log(`list: ${found.accounts.length} account(s), ${docs.length} document(s)${undated ? `, ${undated} without a date` : ""}`);
    if (partial) return { state: "stopped", why: "Schwab said part of its document list did not load.", docs };
    if (undated) return { state: "stopped", why: `${undated} of Schwab's documents had no date that could be read.`, docs };
    return { state: "ok", docs };
  }

  async function readList() {
    if (signedOut()) return { state: "signed_out" };
    if (onStatementsPath()) {
      // The page names itself a moment after it loads.
      const until = Date.now() + WAIT_MS;
      while (!titled() && Date.now() < until) await new Promise((resolve) => setTimeout(resolve, 250));
      if (signedOut()) return { state: "signed_out" };
    }
    if (onStatementsPath() && titled()) return readDocs();
    const body = (document.body && document.body.innerText) || "";
    const screen = `${document.title}\n${body.slice(0, 1500)}`.toLowerCase();
    if (CHALLENGE_MARKERS.some((m) => screen.includes(m))) return { state: "challenge" };
    if (RATE_LIMITS.some((re) => re.test(screen))) {
      return { state: "stopped", why: "Schwab says it is busy or asks to slow down.", docs: [] };
    }
    return { state: "no_list" };
  }

  async function fetchPdf(doc) {
    if (!doc || !doc.documentId) return { ok: false, why: "not a Schwab document" };
    const got = await gateway(`${GATEWAY}/download`, {}, {
      documentId: doc.documentId,
      documentName: doc.documentName || "",
      documentType: doc.documentType || "",
      downloadFileType: "PDF",
    });
    if (got.noToken || got.status === 401 || got.status === 403) {
      return { ok: false, signedOut: true, why: "Schwab asked for a sign-in." };
    }
    if (!got.response) return { ok: false, why: "The document could not be asked for." };
    if (got.status !== 200) return { ok: false, why: `Schwab sent ${got.status}.` };
    const bytes = new Uint8Array(await got.response.arrayBuffer());
    return globalThis.__sweepPdf.pdfResult(bytes);
  }

  globalThis.__sweep = { readList, fetchPdf };
})();
