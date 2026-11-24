import { SITES as PROVIDERS, loadSites } from "./sites.js";
import { DEFAULT_NAMING, checkPattern, normalise, pathFor } from "./naming.js";
import { INBOX, forgetFolders, getFolder, setFolder, withinChosen } from "./folders.js";
import { MAX_FILE_BYTES, exportHistory, fileNameFor, importHistory, planImport, readHistory, recordedCounts } from "./history.js";
import { STORED_PREFIX, storedText } from "./log.js";
import { SHARED_DIR, SHARING_OFF } from "./shared-history.js";

// The sites you added have their own file name patterns too.
await loadSites();

const $ = (id) => document.getElementById(id);
const SAMPLE = { id: "sample", date: "2026-10-28", title: "Monthly Statement" };
const INHERIT = "__inherit", CUSTOM = "__custom";
const FOLDER_PRESETS = [["{provider}", "By Site"], ["{provider}/{year}", "By Site and Year"], ["{year}", "By Year"], ["", "All in One Folder"]];
const FILE_PRESETS = [["", "Each Site's Usual Name"], ["{date} {provider}[ {title}]", "Date, Site, Title"], ["{date:yyyy-mm} {provider}[ {title}]", "Year and Month, Site, Title"], ["{provider} {date}[ {title}]", "Site, Date, Title"]];
const PRESETS = { folder: FOLDER_PRESETS, file: FILE_PRESETS };
let lastPattern = "file";
// A pattern box is shown only when its menu says Custom, or while you are
// writing one, so the usual choices stay two menus.
const open = { folder: false, file: false };

let draft = normalise((await chrome.storage.local.get("naming")).naming);
let dirty = false;
let destination = await getFolder("destination");
let inbox = await getFolder("inbox");

const scope = () => $("scope").value;
const inherit = () => normalise(draft);

function own(id) {
  draft.providers[id] = draft.providers[id] || {};
  return draft.providers[id];
}

// Every pattern, not only those on show, since Save stores them all.
function firstProblem() {
  const checks = [["Main folder", draft.base, false], ["Folder pattern", draft.folder, false], ["File name pattern", draft.file, true]];
  for (const provider of PROVIDERS) {
    const set = draft.providers[provider.id];
    if (set) checks.push([`${provider.name} folder pattern`, set.folder || "", false], [`${provider.name} file name pattern`, set.file || "", true]);
  }
  for (const [label, pattern, isFile] of checks) {
    const problem = checkPattern(pattern, { isFile });
    if (problem) return `${label}: ${problem}`;
  }
  return "";
}

function showStatus(text, kind = "") {
  // The Save bar comes up once there is something to say about saving.
  $("actions").hidden = !text;
  $("status").textContent = text;
  $("status").dataset.kind = kind;
}

// The site the example and the menus' examples are shown for.
const exampleSite = () => PROVIDERS.find((p) => p.id === scope()) || PROVIDERS.find((p) => p.id === "amex") || PROVIDERS[0];

// What one pattern alone makes for the example site, worked out by the same
// code that names real files: the folders for a folder pattern, the name for
// a file one. So a menu says what each choice gives, not only its name.
function resultOf(field, pattern) {
  const parts = pathFor({ base: "", folder: "", file: "", providers: {}, [field]: pattern }, exampleSite(), SAMPLE).split("/");
  const name = parts.pop().replace(/\.pdf$/i, "");
  return field === "file" ? name : parts.join(" / ");
}
// Each choice keeps its short name, and its name with what it gives. The
// closed menu shows the short one (the Example row below says the whole
// path); the open menu shows what each choice gives, to choose by.
function choice(label, field, pattern) {
  const result = checkPattern(pattern, { isFile: field === "file" }) ? "" : resultOf(field, pattern);
  const option = new Option(label, pattern);
  option.dataset.short = label;
  option.dataset.long = result ? `${label}: ${result}` : label;
  return option;
}
const showResults = (menu, on) => {
  for (const option of menu.options) if (option.dataset.long) option.text = on ? option.dataset.long : option.dataset.short;
};

// Rules for one site are kept out of the way until someone asks for them,
// and shown whenever any site already has its own.
let perSite = false;
const hasOwnRules = () => Object.values(draft.providers).some((set) => set && (set.folder || set.file));
function drawScope() {
  const shown = perSite || hasOwnRules() || Boolean(scope());
  $("scopeRow").hidden = !shown;
  $("perSite").hidden = shown;
}

function drawPreview() {
  const problem = firstProblem();
  $("problem").textContent = problem;
  $("save").disabled = Boolean(problem);
  const provider = exampleSite();
  const path = problem ? "" : pathFor(inherit(), provider, SAMPLE);
  $("preview").textContent = path && destination ? `${destination.name}/${withinChosen(destination.name, path)}` : path;
}

function drawLocation() {
  $("destName").textContent = destination ? destination.name : "Downloads Folder";
  $("useDownloads").hidden = !destination;
  $("chooseDest").textContent = destination ? "Change…" : "Choose Folder…";
  $("desthint").textContent = destination ? "" : "Or choose another folder, such as a new one inside Documents.";
  $("inboxRow").hidden = !destination;
  $("shareRow").hidden = !destination;
  $("inboxState").textContent = inbox ? "Allowed" : "Not Allowed Yet";
  $("inboxState").dataset.kind = inbox ? "" : "needed";
  $("allowInbox").textContent = inbox ? "Choose Again…" : "Allow Access…";
  // Once allowed, the steps for allowing it are no longer needed.
  $("inboxhint").textContent = inbox
    ? ""
    : `Some sites save only to Downloads, and pass through a folder there. Choose “${INBOX}” in Downloads when asked.`;
  $("basehint").textContent = destination ? "A folder inside the one you chose. Leave blank to save directly in it." : "Inside Chrome's Downloads folder.";
}

function locationProblem(text) {
  $("locationProblem").textContent = text;
}

// The folder a site's download lands in has to exist before it can be chosen.
function makeInbox() {
  return chrome.downloads.download({
    url: "data:text/plain,Statement%20Sweep%20moves%20documents%20through%20this%20folder.%20It%20is%20safe%20to%20leave%20empty.",
    filename: `${INBOX}/About this folder.txt`, conflictAction: "overwrite", saveAs: false,
  });
}

async function pickFolder(options) {
  try {
    return await window.showDirectoryPicker({ mode: "readwrite", ...options });
  } catch (e) {
    if (e.name !== "AbortError") locationProblem("Chrome would not open that folder. Please choose another.");
    return null;
  }
}

$("chooseDest").addEventListener("click", async () => {
  locationProblem("");
  const handle = await pickFolder({ id: "sweep-destination", startIn: "documents" });
  if (!handle) return;
  await setFolder("destination", handle);
  destination = handle;
  await makeInbox();
  drawLocation();
  drawPreview();
  await shareNow();
});

// Takes in now what other copies saved into the chosen folder (the engine
// does it, background.js), so what is due is right before the first run. An
// engine that does not answer, or is mid-run, leaves it for the next run.
async function shareNow() {
  let r;
  try { r = await chrome.runtime.sendMessage({ type: "syncHistory" }); } catch (e) { return; }
  if (!r || r.none || r.off || r.busy) return;
  const problems = (r.problems || []).map((p) => (p.name ? `${p.name} in ${SHARED_DIR} was not used: ${p.problem}` : p.problem));
  const learned = r.listed ? `Found ${plural(r.listed, "document")} saved into this folder by another copy of Statement Sweep. Each is checked in the folder before it is skipped.` : "";
  historyMessage(problems.join(" "), learned);
  await drawHistory();
}

$("shareHistory").addEventListener("change", async () => {
  await chrome.storage.local.set({ [SHARING_OFF]: !$("shareHistory").checked });
});

$("allowInbox").addEventListener("click", async () => {
  locationProblem("");
  const handle = await pickFolder({ id: "sweep-inbox", startIn: "downloads" });
  if (!handle) return;
  if (handle.name !== INBOX) {
    locationProblem(`Please choose the folder named “${INBOX}” inside Downloads.`);
    return;
  }
  await setFolder("inbox", handle);
  inbox = handle;
  drawLocation();
});

$("useDownloads").addEventListener("click", async () => {
  locationProblem("");
  await forgetFolders();
  destination = null;
  inbox = null;
  drawLocation();
  drawPreview();
});

// The menu above a pattern shows the preset it matches, or Custom.
function drawPreset(field) {
  const id = scope();
  const value = $(field).value;
  const menu = $(`${field}Preset`);
  const presets = PRESETS[field];
  // A preset whose pattern is blank means "inherit" when one site is chosen.
  const shown = id ? presets.filter(([pattern]) => pattern) : presets;
  const general = id ? draft[field] : "";
  const options = [...(id ? [choice("Same as All Sites", field, general)] : []),
    ...shown.map(([pattern, label]) => choice(label, field, pattern)), new Option("Custom", CUSTOM)];
  if (id) options[0].value = INHERIT;
  const known = (id && !value) || shown.some(([pattern]) => pattern === value);
  menu.replaceChildren(...options);
  showResults(menu, document.activeElement === menu && menu.dataset.open === "1");
  menu.value = id && !value ? INHERIT : known ? value : CUSTOM;
  $(field).hidden = !open[field] && menu.value !== CUSTOM;
  // The field buttons and the pattern reference go with the pattern boxes.
  const writing = !$("folder").hidden || !$("file").hidden;
  $("insertRow").hidden = !writing;
  $("patternRef").hidden = !writing;
  $("filehint").hidden = $("file").hidden;
}

function drawFields() {
  open.folder = open.file = false;
  drawScope();
  const id = scope();
  const set = id ? draft.providers[id] || {} : draft;
  $("folder").value = set.folder || (id ? "" : draft.folder);
  $("file").value = set.file || (id ? "" : draft.file);
  $("folder").placeholder = id ? (draft.folder || "No subfolder") : "No subfolder";
  $("file").placeholder = id ? (draft.file || "This site's usual name") : "Each site's usual name";
  $("filehint").textContent = id ? "Blank uses the pattern set for all sites." : "";
  drawPreset("folder");
  drawPreset("file");
  drawPreview();
}

function setPattern(field, value) {
  const id = scope();
  if (id) own(id)[field] = value;
  else draft[field] = value;
}

function changed() {
  dirty = true;
  showStatus("Not saved yet");
  drawPreview();
}

$("base").addEventListener("input", () => { draft.base = $("base").value; changed(); });
for (const field of ["folder", "file"]) {
  $(field).addEventListener("focus", () => { lastPattern = field; });
  $(field).addEventListener("input", () => {
    open[field] = true;
    setPattern(field, $(field).value);
    drawPreset(field);
    changed();
  });
  // Opening the menu (a press, or a key on it) shows what each choice gives.
  const menu = $(`${field}Preset`);
  const reveal = () => { menu.dataset.open = "1"; showResults(menu, true); };
  const conceal = () => { menu.dataset.open = ""; showResults(menu, false); };
  menu.addEventListener("pointerdown", reveal);
  menu.addEventListener("keydown", (e) => { if ([" ", "Enter", "ArrowDown", "ArrowUp", "F4"].includes(e.key) || e.altKey) reveal(); });
  menu.addEventListener("blur", conceal);
  $(`${field}Preset`).addEventListener("change", () => {
    conceal();
    const value = $(`${field}Preset`).value;
    if (value === CUSTOM) {
      // The pattern so far stays, ready to be changed.
      open[field] = true;
      drawPreset(field);
      lastPattern = field;
      $(field).focus();
      return;
    }
    open[field] = false;
    $(field).value = value === INHERIT ? "" : value;
    setPattern(field, $(field).value);
    drawPreset(field);
    changed();
  });
}

// Inserts at the caret of the pattern field last used, keeping its focus.
document.getElementById("insert").addEventListener("mousedown", (e) => { if (e.target.closest(".chip")) e.preventDefault(); });
document.getElementById("insert").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  const input = $(lastPattern);
  const at = input.selectionStart ?? input.value.length;
  input.setRangeText(chip.dataset.insert, at, input.selectionEnd ?? at, "end");
  input.focus();
  input.dispatchEvent(new Event("input", { bubbles: true }));
});
$("scope").addEventListener("change", drawFields);
$("perSite").addEventListener("click", () => {
  perSite = true;
  drawScope();
  $("scope").focus();
});

$("save").addEventListener("click", async () => {
  if (firstProblem()) return;
  await chrome.storage.local.set({ naming: draft });
  dirty = false;
  showStatus("Saved", "ok");
});

$("reset").addEventListener("click", () => {
  draft = normalise({ ...DEFAULT_NAMING, providers: {} });
  $("base").value = draft.base;
  $("scope").value = "";
  drawFields();
  dirty = true;
  showStatus("Defaults restored. Press Save to keep them.");
});

window.addEventListener("beforeunload", (e) => { if (dirty) e.preventDefault(); });

// History: the record of what has been saved, to move to another copy.
const plural = (n, one, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;
let pending = null;

function historyMessage(problem, status = "") {
  $("historyProblem").textContent = problem;
  $("historyStatus").textContent = status;
}

async function drawHistory() {
  const { documents, sites } = await recordedCounts();
  $("historyCount").textContent = documents ? `${plural(documents, "document")} from ${plural(sites, "site")}` : "Nothing yet";
}

// Resolves true once the file is written, false if the person cancelled.
function downloaded(id) {
  const changes = chrome.downloads.onChanged;
  if (!changes) return Promise.resolve(true);
  return new Promise((resolve) => {
    const onChanged = (delta) => {
      if (delta.id !== id || !delta.state || delta.state.current === "in_progress") return;
      changes.removeListener(onChanged);
      resolve(delta.state.current === "complete");
    };
    changes.addListener(onChanged);
  });
}

$("exportHistory").addEventListener("click", async () => {
  historyMessage("");
  const { file, documents, sites } = await exportHistory();
  let id;
  try {
    id = await chrome.downloads.download({
      url: `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(file, null, 1))}`,
      filename: fileNameFor(), conflictAction: "uniquify", saveAs: true,
    });
  } catch (e) {
    // Closing the save window is not a problem.
    if (!/cancel/i.test(e.message || "")) historyMessage("Chrome would not save the file. Please try again.");
    return;
  }
  if (!(await downloaded(id))) return;
  historyMessage("", documents ? `Exported ${plural(documents, "document")} from ${plural(sites, "site")}.` : "Exported. Nothing has been saved yet, so the file holds only your settings.");
});

// Run logs kept in Chrome (log.js), when there was no folder to write them to.
async function storedLogs() {
  const all = await chrome.storage.local.get(null);
  return Object.fromEntries(Object.entries(all).filter(([k, v]) => k.startsWith(STORED_PREFIX) && Array.isArray(v)));
}

async function drawLogs() {
  const kept = await storedLogs();
  const lines = Object.values(kept).reduce((sum, v) => sum + v.length, 0);
  $("logsCount").textContent = lines ? `${plural(lines, "line")} kept in Chrome` : "None kept in Chrome";
  $("exportLogs").disabled = !lines;
}

$("exportLogs").addEventListener("click", async () => {
  historyMessage("");
  const names = new Map(PROVIDERS.map((p) => [p.id, p.name]));
  const text = storedText(await storedLogs(), (id) => names.get(id) || id);
  if (!text) { historyMessage("", "No logs are kept in Chrome."); return; }
  const day = new Date();
  const stamp = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
  let id;
  try {
    id = await chrome.downloads.download({
      url: `data:text/plain;charset=utf-8,${encodeURIComponent(text)}`,
      filename: `Statement Sweep Logs ${stamp}.txt`, conflictAction: "uniquify", saveAs: true,
    });
  } catch (e) {
    if (!/cancel/i.test(e.message || "")) historyMessage("Chrome would not save the file. Please try again.");
    return;
  }
  if (await downloaded(id)) historyMessage("", "Exported the run logs kept in Chrome.");
});

function closeImport() {
  pending = null;
  $("importRow").hidden = true;
  $("importSettings").checked = false;
}

async function drawImport() {
  const plan = await planImport(pending);
  const added = plan.reduce((sum, s) => sum + s.added, 0);
  $("importPlan").replaceChildren(...plan.map((site) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    const count = document.createElement("span");
    name.textContent = site.name;
    count.textContent = site.added ? `${site.added.toLocaleString()} new${site.already ? `, ${site.already.toLocaleString()} already here` : ""}` : "Nothing new";
    li.append(name, count);
    return li;
  }));
  const notes = [];
  if (!plan.length) notes.push("The file has no documents for these sites.");
  else notes.push(added ? `${plural(added, "document")} will be added to the record. Nothing already in it is removed.` : "Everything in the file is already in the record.");
  if (pending.unknown.length) notes.push(`Left out, as this copy does not have ${pending.unknown.length === 1 ? "this site" : "these sites"}: ${pending.unknown.join(", ")}.`);
  // What the file can bring besides the record, said in the box's own label,
  // so it is plain that switched-off sites come with it.
  const brings = [pending.naming && "folder and file name settings", pending.off && "list of switched-off sites"].filter(Boolean);
  const carries = [pending.naming && "its main folder, folder and file name patterns", pending.off && "which sites are switched off"].filter(Boolean);
  if (brings.length && !added) notes.push("Tick the box below to bring in its settings.");
  $("importNote").textContent = notes.join(" ");
  $("importSettingsLabel").textContent = `Also use the file's ${brings.join(" and ")}`;
  // A copy with no record and no settings of its own (a new install, or one
  // loaded from a new folder) takes the file's settings unless you untick it.
  const kept = await chrome.storage.local.get(null);
  const fresh = !Object.keys(kept).some((k) => k.startsWith("seen:")) && kept.naming === undefined && kept.off === undefined;
  $("importSettings").checked = Boolean(brings.length) && fresh;
  $("importSettingsRow").hidden = !carries.length;
  $("importSettingsHint").textContent = carries.length
    ? `Replaces ${carries.join(", and ")} here.${dirty && pending.naming ? " Changes above that are not saved yet are replaced too." : ""}`
    : "";
  pending.added = added;
  $("importConfirm").disabled = !added && !$("importSettings").checked;
  $("importRow").hidden = false;
}

$("importHistory").addEventListener("click", () => {
  historyMessage("");
  $("historyFile").value = "";
  $("historyFile").click();
});

$("historyFile").addEventListener("change", async () => {
  const chosen = $("historyFile").files[0];
  $("historyFile").value = "";
  if (!chosen) return;
  historyMessage("");
  closeImport();
  if (chosen.size > MAX_FILE_BYTES) {
    historyMessage("This file is too large to be a Statement Sweep history file.");
    return;
  }
  let text;
  try { text = await chosen.text(); } catch (e) {
    historyMessage("That file could not be read. Please choose it again.");
    return;
  }
  const read = readHistory(text);
  if (!read.ok) {
    historyMessage(read.problem);
    return;
  }
  pending = read;
  await drawImport();
  $("importConfirm").focus();
});

$("importSettings").addEventListener("change", () => {
  if (pending) $("importConfirm").disabled = !pending.added && !$("importSettings").checked;
});

$("importCancel").addEventListener("click", () => {
  closeImport();
  $("importHistory").focus();
});

$("importConfirm").addEventListener("click", async () => {
  if (!pending) return;
  const result = await importHistory(pending, { useSettings: $("importSettings").checked });
  closeImport();
  const parts = [result.added
    ? `Added ${plural(result.added, "document")} to the record for ${plural(result.sites.filter((s) => s.added).length, "site")}.`
    : "Nothing new to add to the record."];
  if (result.off) parts.push("Sites switched off as in the file.");
  historyMessage("", parts.join(" "));
  if (result.naming) {
    // The page shows what is now saved, as if it had just been opened.
    draft = normalise((await chrome.storage.local.get("naming")).naming);
    $("base").value = draft.base;
    drawFields();
    dirty = false;
    showStatus("Folder and file name settings imported and saved", "ok");
  }
  await drawHistory();
  $("importHistory").focus();
});

$("scope").replaceChildren(new Option("All Sites", ""), ...PROVIDERS.map((p) => new Option(p.name, p.id)));
$("base").value = draft.base;
$("shareHistory").checked = (await chrome.storage.local.get(SHARING_OFF))[SHARING_OFF] !== true;
drawLocation();
drawFields();
drawHistory();
drawLogs();
