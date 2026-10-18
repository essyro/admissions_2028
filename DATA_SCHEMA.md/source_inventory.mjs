import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const schemaDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectDirectory = path.dirname(schemaDirectory);
const sourceDirectory = path.join(projectDirectory, '1_business_source_data');
const manifestPath = path.join(schemaDirectory, 'source_manifest.json');
const universities = [
  ['KU', '건국'], ['KHU', '경희'], ['PNU', '부산'],
  ['SNU', '서울'], ['YSU', '연세'], ['CAU', '중앙'],
];

async function inspectSources() {
  const documents = [];
  const entries = await readdir(sourceDirectory, { withFileTypes: true });
  for (const entry of entries) {
    if (!entry.name.toLowerCase().endsWith('.pdf')) continue;
    assert(entry.isFile(), `PDF must be a regular file, not a symlink: ${entry.name}`);
    const filename = entry.name.normalize('NFC');
    const universityMatches = universities.filter(([, keyword]) => filename.includes(keyword));
    assert.equal(universityMatches.length, 1, `University hint is ambiguous: ${filename}`);
    const contents = await readFile(path.join(sourceDirectory, entry.name));
    assert(contents.subarray(0, 1024).includes(Buffer.from('%PDF-')), `Missing PDF header: ${filename}`);
    const fileHash = createHash('sha256').update(contents).digest('hex');
    const yearHints = [...new Set([...filename.matchAll(/(?<!\d)(20\d{2})(?:학년도|(?=[ _]))/g)].map(match => Number(match[1])))];
    const isStudentGuide = /학생부/.test(filename);
    const isCourseGuide = /권장과목|이수추천과목|과목 선택/.test(filename);
    documents.push({
      document_version_id: `sha256:${fileHash}`,
      file_sha256: fileHash,
      local_path: path.relative(projectDirectory, path.join(sourceDirectory, entry.name)),
      filename_nfc: filename,
      size_bytes: contents.length,
      university_id_hint: universityMatches[0][0],
      document_type_hint: isStudentGuide ? 'student_guide' : isCourseGuide ? 'course_guide' : 'admission_plan',
      filename_year_hints: yearHints,
      curriculum_code_hints: [...new Set([...filename.matchAll(/(2015|2022)(?=개정|및)/g)].map(match => match[1]))],
      review_status: 'unreviewed',
      applicable_admission_years: null,
      publication_date: null,
      revision_label: null,
      pdf_page_count: null,
      review_notes: yearHints.includes(2026) && isStudentGuide
        ? ['Filename indicates 2026; do not treat as 2027 without source review.']
        : [],
    });
  }
  documents.sort((first, second) => first.filename_nfc < second.filename_nfc ? -1 : first.filename_nfc > second.filename_nfc ? 1 : 0);
  assert(documents.length > 0, 'No public PDFs found');
  assert.equal(new Set(documents.map(document => document.filename_nfc)).size, documents.length, 'Duplicate normalized filenames');
  return { schema_version: 1, source_scope: 'public_university_documents', documents };
}

const mode = process.argv[2];
assert(process.argv.length === 3 && ['--create', '--check', '--check-text'].includes(mode), 'Usage: node source_inventory.mjs --create|--check|--check-text');
const manifest = await inspectSources();
if (mode === '--create') {
  await writeFile(manifestPath, JSON.stringify(manifest, null, 2) + '\n', { encoding: 'utf8', flag: 'wx' });
} else {
  assert.deepEqual(JSON.parse(await readFile(manifestPath, 'utf8')), manifest, 'Source files or inventory hints changed; review before replacing the manifest');
}
if (mode === '--check-text') {
  const counts = { no_text: 0, sparse_text: 0, text_present: 0 };
  for (const source of manifest.documents) {
    const filename = path.join(schemaDirectory, 'source_text', source.file_sha256 + '.json');
    const document = JSON.parse(await readFile(filename, 'utf8'));
    assert.equal(document.schema_version, 1);
    assert.equal(document.document_version_id, source.document_version_id);
    assert.equal(document.file_sha256, source.file_sha256);
    assert.equal(document.source_local_path, source.local_path);
    assert.equal(document.filename_nfc, source.filename_nfc);
    assert.equal(document.extraction_method, 'pdfkit_text');
    assert.equal(document.review_status, 'unreviewed');
    assert.equal(document.layout_preserved, false);
    assert(Number.isInteger(document.pdf_page_count) && document.pdf_page_count > 0);
    assert.equal(document.pages.length, document.pdf_page_count);
    document.pages.forEach((page, index) => {
      assert.equal(page.pdf_page, index + 1);
      assert.equal(typeof page.text, 'string');
      assert(Object.hasOwn(counts, page.text_status));
      assert.equal(page.text.trim().length === 0, page.text_status === 'no_text');
      counts[page.text_status]++;
    });
  }
  console.log('PASS: page sequence, document provenance and extraction flags; ' + JSON.stringify(counts));
}
const universityCount = new Set(manifest.documents.map(document => document.university_id_hint)).size;
console.log(`PASS: ${manifest.documents.length} public PDFs, ${universityCount} university hints; mode=${mode}`);
console.log('Content applicability and revision dates remain unreviewed. No database writes performed.');