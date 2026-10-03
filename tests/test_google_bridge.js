const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');

const script = fs.readFileSync(path.join(__dirname, '..', 'apps-script', 'google-bridge', 'Code.js'), 'utf8');
const vector = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'google_bridge_vector.json'), 'utf8'));
const properties = {
  PROTOCOL_VERSION: '1',
  BRIDGE_SECRET: vector.secret,
  ALLOWED_CLOCK_SKEW_SECONDS: '120',
  NONCE_TTL_SECONDS: '300',
  MANIFEST_SPREADSHEET_ID: 'syntheticSheet_123',
  MANIFEST_SHEET: 'Manifest',
  LIBRARY_INDEX_SHEET: 'Library',
  INBOX_FOLDER_ID: 'syntheticInbox_123',
  LIBRARY_FOLDER_ID: 'syntheticLibrary_123',
};
const cache = new Map();
const rows = {
  Manifest: [
    ['ID', 'Title', 'Status', 'Doc ID', 'Ingested', 'Hash'],
    ['ts-example-001', 'Example', 'Ready', 'syntheticDoc_123', '', ''],
    ['draft-001', 'Draft', 'Draft', 'syntheticDraft_123', '', ''],
  ],
  Library: [['ID', 'Title', 'Doc ID', 'Hash', 'Updated', 'Topic', 'Tags', 'Source', 'Status']],
};
const writes = [];
let failNextParagraph = false;
const fakeSheets = Object.fromEntries(Object.entries(rows).map(([name, data]) => [name, {
  getDataRange: () => ({getValues: () => data}),
  getLastRow: () => data.length,
  getRange: (row, column, countRows, countColumns) => ({
    setValue: (value) => { data[row - 1][column - 1] = value; writes.push([name, row, column]); },
    setValues: (values) => {
      for (let i = 0; i < (countColumns || 1); i++) data[row - 1][column - 1 + i] = values[0][i];
      writes.push([name, row, column]);
    },
  }),
  appendRow: (value) => { data.push(value); writes.push([name, data.length, 0]); },
}]));
const files = {
  syntheticDoc_123: {id: 'syntheticDoc_123', parents: ['syntheticInbox_123']},
  syntheticDraft_123: {id: 'syntheticDraft_123', parents: ['syntheticInbox_123']},
};
let nextDoc = 1;
const docs = {};
function fileObject(file) {
  return {
    getId: () => file.id,
    getParents: () => {let i = 0; return {hasNext: () => i < file.parents.length, next: () => ({getId: () => file.parents[i++]})};},
    moveTo: (folder) => { file.parents = [folder.getId()]; },
  };
}
function newDoc(id) {
  const paragraphs = [];
  const kinds = [];
  const body = {
    clear: () => {paragraphs.length = 0; kinds.length = 0;},
    getText: () => paragraphs.join('\n'),
    getNumChildren: () => paragraphs.length,
    getChild: (index) => ({getText: () => paragraphs[index], getHeading: () => kinds[index] || null, ...(kinds[index] === 'LIST' ? {getGlyphType: () => 'BULLET'} : {})}),
    appendParagraph: (line) => {
      if (failNextParagraph) {failNextParagraph = false; throw new Error('synthetic write failure');}
      const index = paragraphs.push(line) - 1;
      kinds[index] = null;
      return {setHeading: (value) => {kinds[index] = value;}};
    },
    appendListItem: (line) => {paragraphs.push(line); kinds.push('LIST');},
  };
  return {
    getId: () => id,
    written: paragraphs,
    setName: () => {},
    saveAndClose: () => {},
    getBody: () => body,
  };
}
const context = vm.createContext({
  PropertiesService: {getScriptProperties: () => ({getProperty: (key) => properties[key] || null})},
  CacheService: {getScriptCache: () => ({get: (key) => cache.get(key), put: (key, value) => cache.set(key, value)})},
  LockService: {getScriptLock: () => ({waitLock: () => {}, releaseLock: () => {}})},
  SpreadsheetApp: {openById: (id) => { assert.strictEqual(id, properties.MANIFEST_SPREADSHEET_ID); return {getSheetByName: (name) => fakeSheets[name]}; }},
  DriveApp: {
    getFileById: (id) => {if (!files[id]) throw new Error('missing file'); return fileObject(files[id]);},
    getFolderById: (id) => ({getId: () => id}),
  },
  DocumentApp: {
    openById: (id) => docs[id] || (docs[id] = newDoc(id)),
    create: () => {const id = 'syntheticLibraryDoc_' + nextDoc++; files[id] = {id, parents: []}; return (docs[id] = newDoc(id));},
    ParagraphHeading: {HEADING1: 'H1', HEADING2: 'H2', HEADING3: 'H3'},
  },
  Utilities: {
    Charset: {UTF_8: 'UTF_8'},
    DigestAlgorithm: {SHA_256: 'SHA_256'},
    computeHmacSha256Signature: (text, secret) => [...crypto.createHmac('sha256', secret).update(text).digest()].map((b) => b > 127 ? b - 256 : b),
    computeDigest: (_algorithm, text) => [...crypto.createHash('sha256').update(text).digest()].map((b) => b > 127 ? b - 256 : b),
  },
  ContentService: {MimeType: {JSON: 'JSON'}, createTextOutput: (text) => ({setMimeType: () => ({text})})},
  console,
});
vm.runInContext(script, context);
const run = (expression) => vm.runInContext(expression, context);
assert.strictEqual(run(`canonical_(${JSON.stringify(vector)})`), vector.canonical);
assert.strictEqual(run(`sign_(${JSON.stringify(vector.secret)}, ${JSON.stringify(vector.canonical)})`), vector.signature);

function request(action, payload, nonce = crypto.randomBytes(16).toString('hex')) {
  const body = {version: 1, action, timestamp: Math.floor(Date.now() / 1000), nonce, payload_json: JSON.stringify(payload)};
  body.signature = crypto.createHmac('sha256', properties.BRIDGE_SECRET).update([body.version, body.action, body.timestamp, body.nonce, body.payload_json].join('\n')).digest('hex');
  return body;
}
function post(body) { return JSON.parse(context.doPost({postData: {contents: JSON.stringify(body)}}).text); }
assert.strictEqual(post(request('ping', {})).ok, true);
let req = request('ping', {});
req.signature = '0'.repeat(64);
assert.strictEqual(post(req).code, 'INVALID_SIGNATURE');
req = request('ping', {}); req.timestamp -= 500; assert.strictEqual(post(req).code, 'STALE_TIMESTAMP');
req = request('ping', {}); req.timestamp += 500; assert.strictEqual(post(req).code, 'STALE_TIMESTAMP');
req = request('ping', {}); assert.strictEqual(post(req).ok, true); assert.strictEqual(post(req).code, 'REUSED_NONCE');
req = request('ping', {}); req.payload_json = '{"tampered":true}'; assert.strictEqual(post(req).code, 'INVALID_SIGNATURE');
req = request('ping', {}); req.version = 2; assert.strictEqual(post(req).code, 'UNSUPPORTED_VERSION');
properties.BRIDGE_SECRET_PREVIOUS = properties.BRIDGE_SECRET;
properties.BRIDGE_SECRET = 'rotated-test-secret';
req = request('ping', {}); req.signature = crypto.createHmac('sha256', properties.BRIDGE_SECRET_PREVIOUS).update([req.version, req.action, req.timestamp, req.nonce, req.payload_json].join('\n')).digest('hex');
assert.strictEqual(post(req).ok, true);

assert.deepStrictEqual([...context.listReady_().map((row) => row.id)], ['ts-example-001']);
const fetched = context.fetchSource_({id: 'ts-example-001', expected_doc_id: 'syntheticDoc_123'});
assert.strictEqual(fetched.origin, 'textstrata-inbox');
assert.throws(() => context.fetchSource_({id: 'ts-example-001', expected_doc_id: 'arbitraryDoc'}), /SOURCE_MISMATCH/);
files.syntheticDoc_123.parents = ['syntheticLibrary_123'];
assert.throws(() => context.fetchSource_({id: 'ts-example-001', expected_doc_id: 'syntheticDoc_123'}), /SOURCE_OUTSIDE_INBOX/);
files.syntheticDoc_123.parents = ['syntheticInbox_123'];
const ack = context.ack_({id: 'ts-example-001', doc_id: 'syntheticDoc_123', expected_status: 'Ready', hash: 'a'.repeat(64)});
assert.strictEqual(ack.status, 'Ingested');
assert.deepStrictEqual(writes.slice(-3).map((w) => w[2]), [3, 5, 6]);
const content = '# Example\n\nBody\n';
const digest = crypto.createHash('sha256').update(content).digest('hex');
const mirror = {id: 'ts-example-001', title: 'Example', content, hash: digest, updated: '', topic: 'Test', tags: ['sample'], source: 'local'};
assert.strictEqual(context.mirrorUpsert_(mirror).action, 'CREATE');
assert.strictEqual(context.mirrorUpsert_(mirror).action, 'UNCHANGED');
assert.strictEqual(context.mirrorStatus_({id: mirror.id}).hash, digest);
rows.Library[1][8] = 'Updated';
const pending = context.listLibraryUpdates_();
assert.strictEqual(pending.length, 1);
const revision = context.fetchLibraryRevision_({id: mirror.id, expected_doc_id: rows.Library[1][2], expected_hash: digest});
assert.strictEqual(revision.content, content);
assert.strictEqual(revision.id, mirror.id);
assert.throws(() => context.fetchLibraryRevision_({id: mirror.id, expected_doc_id: 'arbitraryDoc', expected_hash: digest}), /SOURCE_MISMATCH/);
assert.throws(() => context.mirrorUpsert_(mirror), /LIBRARY_CONFLICT/);
const complete = context.completeLibraryImport_({...mirror, expected_doc_id: rows.Library[1][2], expected_hash: digest, expected_fingerprint: revision.fingerprint});
assert.strictEqual(complete.status, 'Active');
assert.strictEqual(rows.Library[1][8], 'Active');
rows.Library[1][8] = 'Updated';
const changed = context.fetchLibraryRevision_({id: mirror.id, expected_doc_id: rows.Library[1][2], expected_hash: digest});
assert.strictEqual(context.markLibraryConflict_({id: mirror.id, expected_doc_id: rows.Library[1][2], expected_hash: digest, expected_fingerprint: changed.fingerprint}).status, 'Conflict');
assert.throws(() => context.mirrorUpsert_(mirror), /LIBRARY_CONFLICT/);
rows.Library[1][8] = 'Active';
assert.throws(() => context.mirrorUpsert_({...mirror, doc_id: 'arbitraryDoc'}), /INVALID_PAYLOAD/);
assert.throws(() => context.ack_({id: 'wrong-id', doc_id: 'syntheticDoc_123', expected_status: 'Ready', hash: 'a'.repeat(64)}), /RECORD_NOT_FOUND/);
assert.throws(() => context.ack_({id: 'ts-example-001', doc_id: 'wrong-doc', expected_status: 'Ready', hash: 'a'.repeat(64)}), /SOURCE_NOT_READY|SOURCE_MISMATCH/);
const updated = {...mirror, content: '# Example\n\nChanged\n'};
updated.hash = crypto.createHash('sha256').update(updated.content).digest('hex');
assert.strictEqual(context.mirrorUpsert_(updated).action, 'UPDATE');
assert.strictEqual(rows.Library.length, 2);
assert.strictEqual(context.mirrorStatus_({id: mirror.id}).hash, updated.hash);
assert.throws(() => context.mirrorUpsert_({...updated, hash: '0'.repeat(64)}), /HASH_MISMATCH/);
const large = {...updated, content: Array(1200).fill('Synthetic specification line').join('\n')};
large.hash = crypto.createHash('sha256').update(large.content).digest('hex');
assert.strictEqual(context.mirrorUpsert_(large).action, 'UPDATE');
assert.ok(docs[rows.Library[1][2]].written.length < 20);
assert.ok(docs[rows.Library[1][2]].written.join('\n').includes('Origin: textstrata-library\n\nSynthetic specification line'));
for (let i = 9; i < docs[rows.Library[1][2]].written.length; i++) docs[rows.Library[1][2]].written[i] = docs[rows.Library[1][2]].written[i].replace(/\n/g, '\u000b');
rows.Library[1][8] = 'Updated';
assert.strictEqual(context.fetchLibraryRevision_({id: mirror.id, expected_doc_id: rows.Library[1][2], expected_hash: large.hash}).content, large.content + '\n');
rows.Library[1][8] = 'Active';
const structured = '---\nid: ts-example-001\ntitle: Example\ntags:\n- sample\n- jwst\n---\n\n# Example\n\n- First point\n\nBody\n';
const structuredMirror = {...mirror, content: structured, hash: crypto.createHash('sha256').update(structured).digest('hex')};
assert.strictEqual(context.mirrorUpsert_(structuredMirror).action, 'UPDATE');
rows.Library[1][8] = 'Updated';
assert.strictEqual(context.fetchLibraryRevision_({id: mirror.id, expected_doc_id: rows.Library[1][2], expected_hash: structuredMirror.hash}).content, structured);
rows.Library[1][8] = 'Active';
const interrupted = {...large, id: 'ts-example-002', title: 'Interrupted example'};
failNextParagraph = true;
assert.throws(() => context.mirrorUpsert_(interrupted), /synthetic write failure/);
const pendingDocId = rows.Library[2][2];
assert.strictEqual(rows.Library[2][8], 'Pending');
assert.strictEqual(rows.Library[2][3], '');
assert.strictEqual(context.mirrorUpsert_(interrupted).action, 'UPDATE');
assert.strictEqual(rows.Library[2][2], pendingDocId);
assert.strictEqual(rows.Library[2][8], 'Active');
assert.strictEqual(rows.Library[2][3], interrupted.hash);
files[rows.Library[1][2]].parents = ['syntheticInbox_123'];
assert.throws(() => context.mirrorStatus_({id: mirror.id}), /DESTINATION_OUTSIDE_LIBRARY/);
assert.strictEqual(typeof context.deleteSource_, 'undefined');
console.log('Google bridge Apps Script protocol and capability tests passed');
