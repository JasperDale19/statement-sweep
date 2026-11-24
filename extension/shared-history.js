// The record of what has been saved, shared through the folder you chose, so
// copies of Statement Sweep on other computers that choose the same folder
// (in iCloud Drive or Google Drive, say) do not save a document again.
//
// Each site's sweep that saves documents into the folder writes one new
// file, an entry, in the folder's `Statement Sweep History` folder, in the
// export format (history.js): the ids of the documents it saved there, their
// dates, and which are tax forms. Never the folder and file name settings or
// the switched-off sites. An entry is never written over or removed, so two
// computers syncing the folder at once can never undo each other's record,
// and a site one copy does not know stays in the entry of the copy that does.
//
// Before a run, and when the folder is chosen in Settings, every entry is
// read and added to this copy's record with importHistory, which only ever
// adds and never changes a setting. An entry that cannot be read, or is from
// a newer version, is reported and left as it is.
//
// Whatever goes wrong, the worst that follows is a document saved twice: an
// entry that is not written, or not read, only means another copy saves that
// document again. Documents saved before, to Downloads or another folder,
// are never in an entry, so another copy still saves them into this folder.
// `historySharingOff: true` in chrome.storage.local turns it all off.
import { FORMAT, MAX_FILE_BYTES, MAX_IDS, VERSION, importHistory, readHistory } from "./history.js";
import { writeFile } from "./folders.js";

export const SHARED_DIR = "Statement Sweep History";
export const SHARING_OFF = "historySharingOff";
const local = () => chrome.storage.local;
const isObject = (v) => Boolean(v) && typeof v === "object" && !Array.isArray(v);

export const sharingOn = async () => (await local().get(SHARING_OFF))[SHARING_OFF] !== true;

async function readFile(handle) {
  try {
    const got = await handle.getFile();
    return got.size > MAX_FILE_BYTES ? { ok: false, problem: "It is too large to be a Statement Sweep history file." } : readHistory(await got.text());
  } catch (e) {
    return { ok: false, problem: `It could not be read: ${e.name || "Error"}.` };
  }
}

// Every entry in `root` (your chosen folder), added to this copy's record.
// Answers { off: true } when sharing is off, or { read: [names], imported,
// unknown: [site ids], problems: [{name, problem}] }.
export async function readShared(root) {
  if (!(await sharingOn())) return { off: true };
  const result = { read: [], imported: 0, unknown: [], problems: [] };
  const dir = await root.getDirectoryHandle(SHARED_DIR).catch((e) => { if (e.name === "NotFoundError") return null; throw e; });
  if (!dir) return result;
  const handles = [];
  for await (const [name, handle] of dir.entries()) {
    if (handle.kind === "file" && name.endsWith(".json") && !name.startsWith(".")) handles.push([name, handle]);
  }
  handles.sort(([a], [b]) => a.localeCompare(b));
  for (const [name, handle] of handles) {
    const history = await readFile(handle);
    if (!history.ok) {
      result.problems.push({ name, problem: history.problem });
      continue;
    }
    const done = await importHistory(history, { useSettings: false });
    result.read.push(name);
    result.imported += done.added;
    for (const id of done.unknown) if (!result.unknown.includes(id)) result.unknown.push(id);
  }
  return result;
}

const pad = (n) => String(n).padStart(2, "0");
const stamp = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;

// A new entry in `root` for the documents `ids` of site `siteId`, just saved
// into it. Answers the entry's name, or null when sharing is off or there is
// nothing to write. Throws when the entry cannot be written.
export async function writeEntry(root, siteId, ids, now = new Date()) {
  if (!(await sharingOn())) return null;
  const docs = [...new Set(ids.filter((id) => typeof id === "string" && id))];
  if (!docs.length) return null;
  if (docs.length > MAX_IDS) throw new Error(`${docs.length} documents are more than one history file can hold.`);
  const kept = await local().get([`dates:${siteId}`, `tax:${siteId}`]);
  const dates = isObject(kept[`dates:${siteId}`]) ? kept[`dates:${siteId}`] : {};
  const tax = Array.isArray(kept[`tax:${siteId}`]) ? kept[`tax:${siteId}`] : [];
  const file = { format: FORMAT, version: VERSION, exportedAt: now.toISOString(), sites: { [siteId]: docs } };
  const dated = Object.fromEntries(docs.filter((doc) => typeof dates[doc] === "string").map((doc) => [doc, dates[doc]]));
  if (Object.keys(dated).length) file.dates = { [siteId]: dated };
  const taxed = docs.filter((doc) => tax.includes(doc));
  if (taxed.length) file.tax = { [siteId]: taxed };
  const dir = await root.getDirectoryHandle(SHARED_DIR, { create: true });
  // A name of its own: the time, and a random part, so no entry is written over.
  const name = `${stamp(now)} ${crypto.randomUUID()}.json`;
  return writeFile(dir, name, new Blob([JSON.stringify(file)], { type: "application/json" }));
}
