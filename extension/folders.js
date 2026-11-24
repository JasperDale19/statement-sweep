// A folder you chose, outside Chrome's Downloads. Chrome keeps the access in a
// handle, which is stored here and used by the Settings page and the engine.
//
// `destination` is your folder. `inbox` is a folder inside Downloads: a site's
// own download can only land in Downloads, so it lands there under a random
// name and is moved to your folder from there.

export const INBOX = "Statement Sweep Inbox";
const STORE = "handles";

function database() {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("statement-sweep", 1);
    open.onupgradeneeded = () => open.result.createObjectStore(STORE);
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
}

async function withStore(mode, work) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE, mode);
    const request = work(transaction.objectStore(STORE));
    transaction.oncomplete = () => resolve(request.result);
    transaction.onerror = () => reject(transaction.error);
  });
}

export const getFolder = (name) => withStore("readonly", (store) => store.get(name));
export const setFolder = (name, handle) => withStore("readwrite", (store) => store.put(handle, name));
export const forgetFolders = () => withStore("readwrite", (store) => store.clear());

export const isGranted = async (handle) => (await handle.queryPermission({ mode: "readwrite" })) === "granted";
// Needs a press of yours, so it works from a page and not from the engine.
export const askFor = async (handle) => (await isGranted(handle)) || (await handle.requestPermission({ mode: "readwrite" })) === "granted";

// The main folder is made for you inside the one you chose. If you chose one
// with that same name, it is used as is rather than nested inside itself.
export function withinChosen(chosenName, path) {
  const [first, ...rest] = path.split("/");
  return rest.length && first.toLowerCase() === chosenName.toLowerCase() ? rest.join("/") : path;
}

async function freeName(dir, name) {
  const dot = name.lastIndexOf(".");
  const [stem, ext] = dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, ""];
  for (let n = 0; n < 1000; n += 1) {
    const candidate = n ? `${stem} (${n})${ext}` : name;
    try {
      await dir.getFileHandle(candidate);
    } catch (e) {
      if (e.name === "NotFoundError") return candidate;
      throw e;
    }
  }
  throw new Error("Too many files share that name.");
}

// Writes under `path`, making its folders, never over a file already there,
// and only returns once what landed is as long as what was sent. `replace`
// writes over the file of that name instead: only a run's own log (log.js),
// which is written again as the run goes, ever asks for it.
//
// Choosing a free name and making the file are one step, taken one at a time
// (`claim`): two documents saved at once under one name would otherwise both
// find it free, and the second would be written over the first. The file is
// made empty in that step, so the name is taken before the slow write starts.
// A folder that does not answer is given CLAIM_WAIT_MS, then the save fails
// and the next one goes on: a save given up on never writes.
const CLAIM_WAIT_MS = 30000;
let claims = Promise.resolve();
function claim(work) {
  const next = claims.then(() => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("The chosen folder did not answer within 30 seconds.")), CLAIM_WAIT_MS);
    work().then(resolve, reject).finally(() => clearTimeout(timer));
  }));
  claims = next.catch(() => {});
  return next;
}

export async function writeFile(root, path, blob, { replace = false } = {}) {
  const parts = path.split("/");
  const fileName = parts.pop();
  let dir = root;
  for (const part of parts) dir = await dir.getDirectoryHandle(part, { create: true });
  const [name, handle] = await claim(async () => {
    const chosen = replace ? fileName : await freeName(dir, fileName);
    return [chosen, await dir.getFileHandle(chosen, { create: true })];
  });
  const out = await handle.createWritable();
  await out.write(blob);
  await out.close();
  if ((await handle.getFile()).size !== blob.size) throw new Error("The saved copy is incomplete.");
  return [...parts, name].join("/");
}

export async function moveFromInbox(inbox, stageName, root, path) {
  const file = await (await inbox.getFileHandle(stageName)).getFile();
  // A statement must be a PDF by its bytes, whatever the site labelled it.
  if (/\.pdf$/i.test(path)) {
    const head = new Uint8Array(await file.slice(0, 5).arrayBuffer());
    if (String.fromCharCode(...head) !== "%PDF-") throw new Error("The site sent something that is not a PDF.");
  }
  return writeFile(root, path, file);
}
