/* TextStrata bridge. All Google resource IDs come from Script Properties or verified index rows. */
const BRIDGE_VERSION = 1;
const INBOX_HEADERS = ['ID', 'Status', 'Doc ID'];
const LIBRARY_HEADERS = ['ID', 'Title', 'Doc ID', 'Hash', 'Updated', 'Topic', 'Tags', 'Source', 'Status'];
const ACTIONS = ['ping', 'list_ready', 'fetch_source', 'ack', 'mirror_status', 'list_library_status', 'mirror_upsert', 'list_library_updates', 'fetch_library_revision', 'fetch_library_conflict', 'complete_library_import', 'mark_library_conflict', 'resolve_library_conflict'];

function error_(code) { throw new Error(code); }
function property_(name) {
  const value = PropertiesService.getScriptProperties().getProperty(name);
  if (!value) error_('INVALID_CONFIGURATION');
  return value;
}
function output_(value) { return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON); }
function hex_(bytes) { return bytes.map(function (b) { return ('0' + (b & 255).toString(16)).slice(-2); }).join(''); }
function sign_(secret, text) {
  return hex_(Utilities.computeHmacSha256Signature(text, secret, Utilities.Charset.UTF_8));
}
function constantEqual_(left, right) {
  if (typeof left !== 'string' || typeof right !== 'string') return false;
  let difference = left.length ^ right.length;
  const length = Math.max(left.length, right.length);
  for (let i = 0; i < length; i++) difference |= (left.charCodeAt(i) || 0) ^ (right.charCodeAt(i) || 0);
  return difference === 0;
}
function canonical_(request) {
  return [request.version, request.action, request.timestamp, request.nonce, request.payload_json].join('\n');
}
function authenticate_(request) {
  if (Number(property_('PROTOCOL_VERSION')) !== BRIDGE_VERSION) error_('INVALID_CONFIGURATION');
  if (!request || request.version !== BRIDGE_VERSION) error_('UNSUPPORTED_VERSION');
  if (ACTIONS.indexOf(request.action) < 0) error_('UNKNOWN_ACTION');
  if (!Number.isInteger(request.timestamp) || typeof request.nonce !== 'string' || !/^[a-f0-9]{32}$/.test(request.nonce) || typeof request.payload_json !== 'string' || typeof request.signature !== 'string' || !/^[a-f0-9]{64}$/.test(request.signature)) error_('MALFORMED_REQUEST');
  const now = Math.floor(Date.now() / 1000);
  const skew = Math.min(300, Math.max(1, Number(property_('ALLOWED_CLOCK_SKEW_SECONDS')) || 120));
  if (Math.abs(now - request.timestamp) > skew) error_('STALE_TIMESTAMP');
  const message = canonical_(request);
  const current = property_('BRIDGE_SECRET');
  const previous = PropertiesService.getScriptProperties().getProperty('BRIDGE_SECRET_PREVIOUS');
  const valid = constantEqual_(request.signature, sign_(current, message)) || (previous && constantEqual_(request.signature, sign_(previous, message)));
  if (!valid) error_('INVALID_SIGNATURE');
  let payload;
  try { payload = JSON.parse(request.payload_json); } catch (ignored) { error_('MALFORMED_PAYLOAD'); }
  if (!payload || Array.isArray(payload) || typeof payload !== 'object') error_('MALFORMED_PAYLOAD');
  const cache = CacheService.getScriptCache();
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const key = 'nonce-' + hex_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, request.nonce));
    if (cache.get(key)) error_('REUSED_NONCE');
    const ttl = Math.min(600, Math.max(skew * 2 + 5, Number(property_('NONCE_TTL_SECONDS')) || 300));
    cache.put(key, '1', ttl);
  } finally { lock.releaseLock(); }
  return payload;
}
function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents || e.postData.contents.length > 5 * 1024 * 1024) error_('MALFORMED_REQUEST');
    const request = JSON.parse(e.postData.contents);
    const payload = authenticate_(request);
    return output_({ok: true, result: dispatch_(request.action, payload)});
  } catch (exc) {
    const allowed = ['UNSUPPORTED_VERSION', 'UNKNOWN_ACTION', 'MALFORMED_REQUEST', 'MALFORMED_PAYLOAD', 'STALE_TIMESTAMP', 'INVALID_SIGNATURE', 'REUSED_NONCE', 'INVALID_CONFIGURATION', 'MISSING_COLUMN', 'RECORD_NOT_FOUND', 'SOURCE_NOT_READY', 'SOURCE_MISMATCH', 'SOURCE_OUTSIDE_INBOX', 'DESTINATION_OUTSIDE_LIBRARY', 'INVALID_PAYLOAD', 'HASH_MISMATCH', 'LIBRARY_INDEX_CONFLICT', 'LIBRARY_NOT_UPDATED', 'LIBRARY_REVISION_CHANGED', 'LIBRARY_CONFLICT'];
    const code = allowed.indexOf(exc.message) >= 0 ? exc.message : 'BRIDGE_OPERATION_FAILED';
    return output_({ok: false, code: code});
  }
}
function dispatch_(action, payload) {
  if (action === 'ping') return {version: BRIDGE_VERSION, server_time: new Date().toISOString(), capabilities: ACTIONS};
  if (action === 'list_ready') return {records: listReady_()};
  if (action === 'fetch_source') return {record: fetchSource_(payload)};
  if (action === 'ack') return ack_(payload);
  if (action === 'mirror_status') return {record: mirrorStatus_(payload)};
  if (action === 'list_library_status') return {records: listLibraryStatus_(payload)};
  if (action === 'mirror_upsert') return mirrorUpsert_(payload);
  if (action === 'list_library_updates') return {records: listLibraryUpdates_()};
  if (action === 'fetch_library_revision') return {record: fetchLibraryRevision_(payload)};
  if (action === 'fetch_library_conflict') return {record: fetchLibraryConflict_(payload)};
  if (action === 'complete_library_import') return completeLibraryImport_(payload);
  if (action === 'mark_library_conflict') return markLibraryConflict_(payload);
  if (action === 'resolve_library_conflict') return resolveLibraryConflict_(payload);
  error_('UNKNOWN_ACTION');
}
function manifestSheet_() {
  const sheet = SpreadsheetApp.openById(property_('MANIFEST_SPREADSHEET_ID')).getSheetByName(property_('MANIFEST_SHEET'));
  if (!sheet) error_('INVALID_CONFIGURATION');
  return sheet;
}
function librarySheet_() {
  if (property_('INBOX_FOLDER_ID') === property_('LIBRARY_FOLDER_ID')) error_('INVALID_CONFIGURATION');
  const sheet = SpreadsheetApp.openById(property_('MANIFEST_SPREADSHEET_ID')).getSheetByName(property_('LIBRARY_INDEX_SHEET'));
  if (!sheet) error_('INVALID_CONFIGURATION');
  return sheet;
}
function table_(sheet, required) {
  const values = sheet.getDataRange().getValues();
  const headers = (values[0] || []).map(function (v) { return String(v).trim(); });
  required.forEach(function (name) { if (headers.indexOf(name) < 0) error_('MISSING_COLUMN'); });
  return {sheet: sheet, headers: headers, rows: values.slice(1).map(function (raw, index) {
    const row = {row: index + 2};
    headers.forEach(function (key, col) { row[key] = raw[col]; });
    return row;
  })};
}
function matchingRow_(table, id) {
  if (typeof id !== 'string' || !/^[a-zA-Z0-9._-]+$/.test(id)) error_('INVALID_PAYLOAD');
  const found = table.rows.filter(function (row) { return String(row.ID).trim() === id; });
  if (found.length > 1) error_('LIBRARY_INDEX_CONFLICT');
  return found[0] || null;
}
function ready_(status) { return ['ready', 'updated'].indexOf(String(status).trim().toLowerCase()) >= 0; }
function listReady_() {
  return table_(manifestSheet_(), INBOX_HEADERS).rows.filter(function (row) {
    return ready_(row.Status) && String(row.ID).trim() && String(row['Doc ID']).trim();
  }).map(function (row) {
    return {id: String(row.ID).trim(), doc_id: String(row['Doc ID']).trim(), status: String(row.Status).trim(), title: String(row.Title || ''), updated: String(row.Updated || '')};
  });
}
function inFolder_(file, folderId) {
  const parents = file.getParents();
  while (parents.hasNext()) if (parents.next().getId() === folderId) return true;
  return false;
}
function sourceRow_(payload) {
  if (Object.keys(payload).some(function (key) { return ['id', 'expected_doc_id', 'doc_id', 'expected_status', 'hash'].indexOf(key) < 0; })) error_('INVALID_PAYLOAD');
  const row = matchingRow_(table_(manifestSheet_(), INBOX_HEADERS), payload.id);
  if (!row) error_('RECORD_NOT_FOUND');
  if (!ready_(row.Status)) error_('SOURCE_NOT_READY');
  if (String(row['Doc ID']).trim() !== String(payload.expected_doc_id || payload.doc_id || '')) error_('SOURCE_MISMATCH');
  if (property_('INBOX_FOLDER_ID') === property_('LIBRARY_FOLDER_ID')) error_('INVALID_CONFIGURATION');
  const file = DriveApp.getFileById(String(row['Doc ID']).trim());
  if (!inFolder_(file, property_('INBOX_FOLDER_ID'))) error_('SOURCE_OUTSIDE_INBOX');
  if (inFolder_(file, property_('LIBRARY_FOLDER_ID'))) error_('SOURCE_OUTSIDE_INBOX');
  return row;
}
function fetchSource_(payload) {
  if (!payload.expected_doc_id) error_('INVALID_PAYLOAD');
  const row = sourceRow_(payload);
  return {id: String(row.ID).trim(), doc_id: String(row['Doc ID']).trim(), title: String(row.Title || ''), content: DocumentApp.openById(String(row['Doc ID']).trim()).getBody().getText(), created: String(row.Created || ''), updated: String(row.Updated || ''), topic: String(row.Topic || ''), tags: String(row.Tags || '').split(/[,;]/).map(function (v) { return v.trim(); }).filter(Boolean), origin: 'textstrata-inbox'};
}
function ack_(payload) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const row = sourceRow_(payload);
    if (String(row.Status).trim() !== String(payload.expected_status || '').trim()) error_('SOURCE_MISMATCH');
    if (!/^[a-f0-9]{64}$/.test(String(payload.hash || ''))) error_('INVALID_PAYLOAD');
    const table = table_(manifestSheet_(), INBOX_HEADERS.concat(['Ingested', 'Hash']));
    const now = new Date().toISOString();
    table.sheet.getRange(row.row, table.headers.indexOf('Status') + 1).setValue('Ingested');
    table.sheet.getRange(row.row, table.headers.indexOf('Ingested') + 1).setValue(now);
    table.sheet.getRange(row.row, table.headers.indexOf('Hash') + 1).setValue(payload.hash);
    return {status: 'Ingested', ingested: now, hash: payload.hash};
  } finally { lock.releaseLock(); }
}
function mirrorStatus_(payload) {
  if (Object.keys(payload).join(',') !== 'id') error_('INVALID_PAYLOAD');
  const row = matchingRow_(table_(librarySheet_(), LIBRARY_HEADERS), payload.id);
  if (row) {
    if (!row['Doc ID']) error_('DESTINATION_OUTSIDE_LIBRARY');
    const file = DriveApp.getFileById(String(row['Doc ID']));
    if (!inFolder_(file, property_('LIBRARY_FOLDER_ID')) || inFolder_(file, property_('INBOX_FOLDER_ID'))) error_('DESTINATION_OUTSIDE_LIBRARY');
  }
  return row ? {id: String(row.ID), doc_id: String(row['Doc ID']), hash: String(row.Hash), updated: String(row.Updated), status: String(row.Status)} : null;
}
function listLibraryStatus_(payload) {
  if (Object.keys(payload).length !== 0) error_('INVALID_PAYLOAD');
  const seen = {};
  return table_(librarySheet_(), LIBRARY_HEADERS).rows.filter(function (row) {
    return String(row.ID).trim();
  }).map(function (row) {
    const id = String(row.ID).trim();
    if (!/^[a-zA-Z0-9._-]+$/.test(id) || seen[id]) error_('LIBRARY_INDEX_CONFLICT');
    seen[id] = true;
    const docId = String(row['Doc ID']).trim();
    if (!docId) error_('DESTINATION_OUTSIDE_LIBRARY');
    const file = DriveApp.getFileById(docId);
    if (!inFolder_(file, property_('LIBRARY_FOLDER_ID')) || inFolder_(file, property_('INBOX_FOLDER_ID'))) error_('DESTINATION_OUTSIDE_LIBRARY');
    return {id: id, doc_id: docId, hash: String(row.Hash), status: String(row.Status)};
  });
}
function libraryRevisionRow_(payload, expectedStatus) {
  if (Object.keys(payload).some(function (key) { return ['id', 'expected_doc_id', 'expected_hash', 'expected_fingerprint'].indexOf(key) < 0; })) error_('INVALID_PAYLOAD');
  const row = matchingRow_(table_(librarySheet_(), LIBRARY_HEADERS), payload.id);
  if (!row) error_('RECORD_NOT_FOUND');
  if (String(row.Status).trim().toLowerCase() !== (expectedStatus || 'updated')) error_('LIBRARY_NOT_UPDATED');
  if (String(row['Doc ID']).trim() !== String(payload.expected_doc_id || '')) error_('SOURCE_MISMATCH');
  if (!/^[a-f0-9]{64}$/.test(String(row.Hash)) || String(row.Hash) !== String(payload.expected_hash || '')) error_('LIBRARY_REVISION_CHANGED');
  const file = DriveApp.getFileById(String(row['Doc ID']));
  if (!inFolder_(file, property_('LIBRARY_FOLDER_ID')) || inFolder_(file, property_('INBOX_FOLDER_ID'))) error_('DESTINATION_OUTSIDE_LIBRARY');
  return row;
}
function listLibraryUpdates_() {
  return table_(librarySheet_(), LIBRARY_HEADERS).rows.filter(function (row) {
    return String(row.Status).trim().toLowerCase() === 'updated' && String(row.ID).trim() && String(row['Doc ID']).trim();
  }).map(function (row) {
    return {id: String(row.ID).trim(), doc_id: String(row['Doc ID']).trim(), hash: String(row.Hash).trim()};
  });
}
function docFingerprint_(document) {
  return hex_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, document.getBody().getText(), Utilities.Charset.UTF_8));
}
function libraryBody_(document, id) {
  const body = document.getBody();
  const count = body.getNumChildren();
  let sawId = false;
  let contentStart = -1;
  for (let i = 0; i < Math.min(count, 16); i++) {
    const line = body.getChild(i).getText().trim();
    if (line === 'TextStrata ID: ' + id) sawId = true;
    if (line === 'Origin: textstrata-library') { contentStart = i + 1; break; }
  }
  if (!sawId || contentStart < 0 || contentStart >= count) error_('LIBRARY_CONFLICT');
  const parts = [];
  for (let i = contentStart; i < count; i++) {
    const element = body.getChild(i);
    let value = element.getText().replace(/\u000b/g, '\n').replace(/\r\n?/g, '\n');
    if (!parts.length && !value.trim()) continue;
    const heading = typeof element.getHeading === 'function' ? element.getHeading() : null;
    const headings = [DocumentApp.ParagraphHeading.HEADING1, DocumentApp.ParagraphHeading.HEADING2, DocumentApp.ParagraphHeading.HEADING3];
    if (headings.indexOf(heading) >= 0) value = '#'.repeat(headings.indexOf(heading) + 1) + ' ' + value;
    else if (typeof element.getGlyphType === 'function') value = '- ' + value;
    parts.push(value.replace(/\n$/, ''));
  }
  return parts.join('\n').replace(/\n*$/, '') + '\n';
}
function readLibraryRevision_(payload, expectedStatus) {
  const row = libraryRevisionRow_(payload, expectedStatus);
  const document = DocumentApp.openById(String(row['Doc ID']));
  return {id: String(row.ID), doc_id: String(row['Doc ID']), hash: String(row.Hash), fingerprint: docFingerprint_(document), content: libraryBody_(document, String(row.ID)), title: String(row.Title || ''), topic: String(row.Topic || ''), tags: String(row.Tags || ''), source: String(row.Source || ''), status: expectedStatus === 'conflict' ? 'Conflict' : 'Updated'};
}
function fetchLibraryRevision_(payload) { return readLibraryRevision_(payload, 'updated'); }
function fetchLibraryConflict_(payload) { return readLibraryRevision_(payload, 'conflict'); }
function markLibraryConflict_(payload) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const row = libraryRevisionRow_(payload);
    const document = DocumentApp.openById(String(row['Doc ID']));
    if (docFingerprint_(document) !== payload.expected_fingerprint) error_('LIBRARY_REVISION_CHANGED');
    const table = table_(librarySheet_(), LIBRARY_HEADERS);
    table.sheet.getRange(row.row, table.headers.indexOf('Status') + 1).setValue('Conflict');
    return {status: 'Conflict'};
  } finally { lock.releaseLock(); }
}
function completeLibraryImport_(payload) {
  const allowed = ['id', 'expected_doc_id', 'expected_hash', 'expected_fingerprint', 'title', 'content', 'hash', 'updated', 'topic', 'tags', 'source'];
  if (Object.keys(payload).some(function (key) { return allowed.indexOf(key) < 0; }) || typeof payload.content !== 'string' || typeof payload.title !== 'string' || !Array.isArray(payload.tags) || !/^[a-f0-9]{64}$/.test(String(payload.hash))) error_('INVALID_PAYLOAD');
  const actual = hex_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, payload.content, Utilities.Charset.UTF_8));
  if (!constantEqual_(actual, payload.hash)) error_('HASH_MISMATCH');
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const row = libraryRevisionRow_({id: payload.id, expected_doc_id: payload.expected_doc_id, expected_hash: payload.expected_hash, expected_fingerprint: payload.expected_fingerprint});
    const document = DocumentApp.openById(String(row['Doc ID']));
    if (docFingerprint_(document) !== payload.expected_fingerprint) error_('LIBRARY_REVISION_CHANGED');
    writeArticle_(document, payload);
    const table = table_(librarySheet_(), LIBRARY_HEADERS);
    const values = {Title: payload.title, Hash: payload.hash, Updated: new Date().toISOString(), Topic: String(payload.topic || ''), Tags: payload.tags.join(', '), Source: String(payload.source || ''), Status: 'Active'};
    Object.keys(values).forEach(function (name) { table.sheet.getRange(row.row, table.headers.indexOf(name) + 1).setValue(values[name]); });
    return {status: 'Active', doc_id: String(row['Doc ID']), hash: payload.hash};
  } finally { lock.releaseLock(); }
}
function resolveLibraryConflict_(payload) {
  const allowed = ['id', 'expected_doc_id', 'expected_hash', 'expected_fingerprint', 'title', 'content', 'hash', 'updated', 'topic', 'tags', 'source', 'resolution', 'reason'];
  if (Object.keys(payload).some(function (key) { return allowed.indexOf(key) < 0; }) ||
      ['keep_local', 'keep_google', 'manual_merge'].indexOf(payload.resolution) < 0 ||
      typeof payload.reason !== 'string' || !payload.reason.trim() ||
      typeof payload.content !== 'string' || typeof payload.title !== 'string' ||
      !Array.isArray(payload.tags) || !/^[a-f0-9]{64}$/.test(String(payload.hash))) error_('INVALID_PAYLOAD');
  const actual = hex_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, payload.content, Utilities.Charset.UTF_8));
  if (!constantEqual_(actual, payload.hash)) error_('HASH_MISMATCH');
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const row = libraryRevisionRow_({id: payload.id, expected_doc_id: payload.expected_doc_id, expected_hash: payload.expected_hash, expected_fingerprint: payload.expected_fingerprint}, 'conflict');
    const document = DocumentApp.openById(String(row['Doc ID']));
    if (docFingerprint_(document) !== payload.expected_fingerprint) error_('LIBRARY_REVISION_CHANGED');
    writeArticle_(document, payload);
    const table = table_(librarySheet_(), LIBRARY_HEADERS);
    const values = {Title: payload.title, Hash: payload.hash, Updated: new Date().toISOString(), Topic: String(payload.topic || ''), Tags: payload.tags.join(', '), Source: String(payload.source || ''), Status: 'Active'};
    Object.keys(values).forEach(function (name) { table.sheet.getRange(row.row, table.headers.indexOf(name) + 1).setValue(values[name]); });
    return {status: 'Active', doc_id: String(row['Doc ID']), hash: payload.hash};
  } finally { lock.releaseLock(); }
}
function mirrorUpsert_(payload) {
  const allowed = ['id', 'title', 'content', 'hash', 'updated', 'topic', 'tags', 'source'];
  if (Object.keys(payload).some(function (key) { return allowed.indexOf(key) < 0; }) || typeof payload.content !== 'string' || typeof payload.title !== 'string' || !Array.isArray(payload.tags) || !/^[a-f0-9]{64}$/.test(String(payload.hash || ''))) error_('INVALID_PAYLOAD');
  const expected = hex_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, payload.content, Utilities.Charset.UTF_8));
  if (!constantEqual_(expected, payload.hash)) error_('HASH_MISMATCH');
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const table = table_(librarySheet_(), LIBRARY_HEADERS);
    const row = matchingRow_(table, payload.id);
    if (row && String(row.Status).trim() !== 'Active' && String(row.Status).trim() !== 'Pending') error_('LIBRARY_CONFLICT');
    if (row && String(row.Hash) === payload.hash) return {action: 'UNCHANGED', doc_id: String(row['Doc ID'])};
    const folder = DriveApp.getFolderById(property_('LIBRARY_FOLDER_ID'));
    let document;
    let action;
    if (row) {
      if (!row['Doc ID']) error_('LIBRARY_INDEX_CONFLICT');
      const file = DriveApp.getFileById(String(row['Doc ID']));
      if (!inFolder_(file, folder.getId()) || inFolder_(file, property_('INBOX_FOLDER_ID'))) error_('DESTINATION_OUTSIDE_LIBRARY');
      document = DocumentApp.openById(file.getId());
      action = 'UPDATE';
    } else {
      document = DocumentApp.create(payload.title);
      DriveApp.getFileById(document.getId()).moveTo(folder);
      action = 'CREATE';
      table.sheet.appendRow([payload.id, payload.title, document.getId(), '', '', String(payload.topic || ''), payload.tags.join(', '), String(payload.source || 'local'), 'Pending']);
    }
    writeArticle_(document, payload);
    const values = [payload.id, payload.title, document.getId(), payload.hash, new Date().toISOString(), String(payload.topic || ''), payload.tags.join(', '), String(payload.source || 'local'), 'Active'];
    if (row) LIBRARY_HEADERS.forEach(function (name, index) { table.sheet.getRange(row.row, table.headers.indexOf(name) + 1).setValue(values[index]); });
    else {
      const rowNumber = table.sheet.getLastRow();
      ['Hash', 'Updated', 'Status'].forEach(function (name) {
        const index = LIBRARY_HEADERS.indexOf(name);
        table.sheet.getRange(rowNumber, table.headers.indexOf(name) + 1).setValue(values[index]);
      });
    }
    return {action: action, doc_id: document.getId()};
  } finally { lock.releaseLock(); }
}
function writeArticle_(document, article) {
  document.setName(article.title);
  const body = document.getBody();
  const header = ['TextStrata ID: ' + article.id, 'Title: ' + article.title, 'Updated: ' + String(article.updated || ''), 'Topic: ' + String(article.topic || ''), 'Tags: ' + article.tags.join(', '), 'Source: ' + String(article.source || 'local'), 'Content Hash: ' + article.hash, 'Origin: textstrata-library'];
  const content = String(article.content);
  if (content.length > 100000 || content.split('\n').length > 1000) {
    body.clear();
    header.forEach(function (line) { body.appendParagraph(line); });
    body.appendParagraph('');
    let remaining = content;
    while (remaining.length) {
      let cut = Math.min(16000, remaining.length);
      if (cut < remaining.length) {
        const lineBreak = remaining.lastIndexOf('\n', cut);
        if (lineBreak > 8000) cut = lineBreak + 1;
      }
      body.appendParagraph(remaining.slice(0, cut));
      remaining = remaining.slice(cut);
    }
    document.saveAndClose();
    return;
  }
  body.clear();
  header.forEach(function (line) { body.appendParagraph(line); });
  body.appendParagraph('');
  content.split('\n').forEach(function (line) {
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      const p = body.appendParagraph(heading[2]);
      p.setHeading([DocumentApp.ParagraphHeading.HEADING1, DocumentApp.ParagraphHeading.HEADING2, DocumentApp.ParagraphHeading.HEADING3][heading[1].length - 1]);
    } else if (/^[-*]\s+/.test(line)) body.appendListItem(line.replace(/^[-*]\s+/, ''));
    else body.appendParagraph(line);
  });
  document.saveAndClose();
}
