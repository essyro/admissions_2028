import argparse
import base64
import getpass
import hashlib
import json
import os
import sqlite3
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parent
TX_BASE = 'http://127.0.0.1:7474/db/admissions2028/tx'
OWNER = 'admissions2028_public_sources_v1'
IDENTITY_CONSTRAINT = 'admissions2028_public_identity_v1'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def build_snapshot(root=ROOT):
    manifest = json.loads((root / 'source_manifest.json').read_text(encoding='utf-8'))
    require(manifest['source_scope'] == 'public_university_documents' and manifest['schema_version'] == 1, 'Unsupported manifest')
    allowed = (root.parent / '1_business_source_data').resolve()
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    connection.executescript((root / '01_admissions.sql').read_text(encoding='utf-8'))
    known_universities = {row[0] for row in connection.execute('SELECT university_id FROM counseling_university')}
    page_total = 0
    try:
        with connection:
            for source in manifest['documents']:
                source_path = root.parent / source['local_path']
                require(not source_path.is_symlink() and source_path.resolve().parent == allowed, 'Source outside allowed public directory')
                digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
                require(digest == source['file_sha256'], 'Source hash mismatch')
                require(source['document_version_id'] == 'sha256:' + digest, 'Source identity mismatch')
                require(source['university_id_hint'] in known_universities, 'University outside counseling scope')
                document = json.loads((root / 'source_text' / (digest + '.json')).read_text(encoding='utf-8'))
                require(document['file_sha256'] == digest and document['document_version_id'] == source['document_version_id'], 'Extraction identity mismatch')
                require(document['source_local_path'] == source['local_path'], 'Extraction path mismatch')
                require(document['review_status'] == 'unreviewed' and not document['layout_preserved'], 'Unexpected extraction state')
                require(document['extraction_method'] == 'pdfkit_text', 'Unsupported extraction method')
                require(document['pdf_page_count'] > 0 and len(document['pages']) == document['pdf_page_count'], 'Missing PDF pages')
                page_total += document['pdf_page_count']
                document_id = 'document:' + digest
                version_id = source['document_version_id']
                connection.execute('INSERT INTO source_document VALUES (?, ?, ?, ?)', (document_id, source['university_id_hint'], source['document_type_hint'], source['filename_nfc']))
                connection.execute('INSERT INTO source_document_version (document_version_id, document_id, file_sha256, local_path, extraction_method) VALUES (?, ?, ?, ?, ?)', (version_id, document_id, digest, source['local_path'], 'pdfkit_text'))
                for page_number, page in enumerate(document['pages'], 1):
                    require(page['pdf_page'] == page_number and isinstance(page['text'], str), 'Invalid page sequence or text')
                    if not page['text'].strip():
                        continue
                    fragment_id = f'fragment:{digest}:{page_number}'
                    evidence_id = f'evidence:{digest}:{page_number}'
                    connection.execute('INSERT INTO source_fragment (fragment_id, document_version_id, pdf_page, quote) VALUES (?, ?, ?, ?)', (fragment_id, version_id, page_number, page['text']))
                    connection.execute('INSERT INTO evidence_set (evidence_set_id) VALUES (?)', (evidence_id,))
                    connection.execute('INSERT INTO evidence_set_item VALUES (?, ?)', (evidence_id, fragment_id))
        payload = {
            'University': [dict(row) for row in connection.execute("SELECT 'university:' || university_id AS admissions_id, name, 'draft' AS review_status FROM university ORDER BY university_id")],
            'DocumentVersion': [dict(row) for row in connection.execute("SELECT version.document_version_id AS admissions_id, 'university:' || document.university_id AS university_admissions_id, document.title, document.document_type, version.file_sha256, version.local_path, version.extraction_method, version.review_status FROM source_document_version AS version JOIN source_document AS document USING(document_id) ORDER BY version.document_version_id")],
            'EvidenceFragment': [dict(row) for row in connection.execute("SELECT fragment_id AS admissions_id, document_version_id AS document_admissions_id, pdf_page, quote, 'draft' AS review_status FROM source_fragment ORDER BY fragment_id")],
        }
        require(payload['DocumentVersion'] and payload['EvidenceFragment'], 'No source evidence to load')
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        release_id = OWNER + '_' + hashlib.sha256(content).hexdigest()
        connection.execute('INSERT INTO data_release (release_id) VALUES (?)', (release_id,))
        connection.commit()
        require(not connection.execute('PRAGMA foreign_key_check').fetchall(), 'SQLite foreign key check failed')
        require(connection.execute('SELECT COUNT(*) FROM v_student_record_candidates').fetchone()[0] == 0, 'Unreviewed sources cannot be recommendation candidates')
        return connection, release_id, payload, page_total
    except Exception:
        connection.close()
        raise


def expected_counts(payload):
    return {
        'nodes': {label: len(rows) for label, rows in payload.items()},
        'relationships': {'HAS_DOCUMENT': len(payload['DocumentVersion']), 'IN_DOCUMENT': len(payload['EvidenceFragment'])},
    }


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class Neo4jClient:
    def __init__(self, username, password):
        self.authorization = 'Basic ' + base64.b64encode((username + ':' + password).encode('utf-8')).decode('ascii')
        self.opener = build_opener(NoRedirect())

    def request(self, url, statements=(), method='POST'):
        require(url == TX_BASE or url == TX_BASE + '/commit' or (url.startswith(TX_BASE + '/') and url.removeprefix(TX_BASE + '/').removesuffix('/commit').isdigit()), 'Unexpected transaction URL')
        data = None if method == 'DELETE' else json.dumps({'statements': list(statements)}, ensure_ascii=False).encode('utf-8')
        request = Request(url, data=data, method=method, headers={'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': self.authorization})
        try:
            with self.opener.open(request, timeout=90) as response:
                body = json.load(response)
        except HTTPError as error:
            raise RuntimeError(f'Neo4j HTTP {error.code}; credentials were not logged. No automatic retry.') from None
        except URLError:
            raise RuntimeError('Neo4j connection failed. If this occurred during commit, the outcome is unknown; verify before retrying.') from None
        errors = body.get('errors', [])
        require(not errors, 'Neo4j rejected request: ' + ', '.join(error.get('code', 'unknown') for error in errors))
        return body

    def query(self, statement, parameters=None, url=TX_BASE + '/commit'):
        return self.request(url, [{'statement': statement, 'parameters': parameters or {}, 'resultDataContents': ['row']}])


def rows(body, result_index=0):
    result = body['results'][result_index]
    return [dict(zip(result['columns'], record['row'])) for record in result['data']]


def preflight(client):
    require(rows(client.query('CALL db.info() YIELD name RETURN name')) == [{'name': 'admissions2028'}], 'Wrong target database')
    foreign = rows(client.query('MATCH (node:Admissions2028) WHERE node.owner_key IS NULL OR node.owner_key <> $owner RETURN count(node) AS count', {'owner': OWNER}))[0]['count']
    require(foreign == 0, 'Existing Admissions2028 namespace belongs to another source. Manual review required; nothing overwritten.')
    constraints = rows(client.query('SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties RETURN name, type, labelsOrTypes, properties'))
    for constraint in constraints:
        if 'Admissions2028' in constraint['labelsOrTypes'] or constraint['name'] == IDENTITY_CONSTRAINT:
            require(constraint['labelsOrTypes'] == ['Admissions2028'] and set(constraint['properties']) == {'admissions_id', 'release_id'} and constraint['type'] in ('UNIQUENESS', 'NODE_PROPERTY_UNIQUENESS'), 'Existing identity constraint needs manual review')
    return rows(client.query('MATCH (node) RETURN count(node) AS count'))[0]['count']


def verify_graph(client, release_id, payload, url=TX_BASE + '/commit'):
    parameters = {'owner': OWNER, 'release': release_id}
    nodes = rows(client.query('MATCH (node:Admissions2028 {owner_key: $owner, release_id: $release}) RETURN labels(node) AS labels, count(node) AS count', parameters, url))
    actual_nodes = {}
    for entry in nodes:
        labels = set(entry['labels']) - {'Admissions2028'}
        require(len(labels) == 1 and next(iter(labels)) in payload, 'Unexpected node labels in owned release')
        label = next(iter(labels))
        actual_nodes[label] = actual_nodes.get(label, 0) + entry['count']
    relationships = rows(client.query('MATCH (node:Admissions2028 {owner_key: $owner, release_id: $release})-[link]->() RETURN type(link) AS type, count(link) AS count', parameters, url))
    actual = {'nodes': actual_nodes, 'relationships': {entry['type']: entry['count'] for entry in relationships}}
    require(actual == expected_counts(payload), 'Graph count mismatch; incomplete or unexpected relationships')
    for label, expected in payload.items():
        records = rows(client.query(f'MATCH (node:Admissions2028:{label} {{owner_key: $owner, release_id: $release}}) RETURN properties(node) AS properties', parameters, url))
        stored = []
        for record in records:
            properties = record['properties']
            properties.pop('owner_key')
            properties.pop('release_id')
            stored.append(properties)
        require(sorted(stored, key=lambda item: item['admissions_id']) == sorted(expected, key=lambda item: item['admissions_id']), 'Stored source properties differ from SQLite snapshot')
    endpoints = rows(client.query('MATCH (node:Admissions2028 {owner_key: $owner, release_id: $release})-[link]->(target) WHERE NOT target:Admissions2028 OR target.owner_key IS NULL OR target.owner_key <> $owner OR target.release_id IS NULL OR target.release_id <> $release OR (type(link) = "HAS_DOCUMENT" AND (NOT node:University OR NOT target:DocumentVersion OR target.university_admissions_id <> node.admissions_id)) OR (type(link) = "IN_DOCUMENT" AND (NOT node:EvidenceFragment OR NOT target:DocumentVersion OR node.document_admissions_id <> target.admissions_id)) RETURN count(link) AS count', parameters, url))[0]['count']
    require(endpoints == 0, 'Relationship ownership or source endpoint mismatch')
    return actual


def apply_graph(client, release_id, payload):
    before_count = preflight(client)
    client.query(f'CREATE CONSTRAINT {IDENTITY_CONSTRAINT} IF NOT EXISTS FOR (node:Admissions2028) REQUIRE (node.admissions_id, node.release_id) IS UNIQUE')
    opened = client.request(TX_BASE)
    commit_url = opened['commit']
    transaction_url = commit_url.removesuffix('/commit')
    try:
        for label, records in payload.items():
            for offset in range(0, len(records), 100):
                query = f'UNWIND $records AS record MERGE (node:Admissions2028:{label} {{admissions_id: record.admissions_id, release_id: $release}}) ON CREATE SET node += record, node.owner_key = $owner RETURN count(node) AS matched'
                matched = rows(client.query(query, {'records': records[offset:offset + 100], 'release': release_id, 'owner': OWNER}, transaction_url))[0]['matched']
                require(matched == len(records[offset:offset + 100]), 'Node count mismatch during MERGE')
        parameters = {'owner': OWNER, 'release': release_id}
        client.query('MATCH (university:Admissions2028:University {owner_key: $owner, release_id: $release}), (document:Admissions2028:DocumentVersion {owner_key: $owner, release_id: $release}) WHERE document.university_admissions_id = university.admissions_id MERGE (university)-[:HAS_DOCUMENT]->(document)', parameters, transaction_url)
        client.query('MATCH (fragment:Admissions2028:EvidenceFragment {owner_key: $owner, release_id: $release}), (document:Admissions2028:DocumentVersion {owner_key: $owner, release_id: $release}) WHERE fragment.document_admissions_id = document.admissions_id MERGE (fragment)-[:IN_DOCUMENT]->(document)', parameters, transaction_url)
        verify_graph(client, release_id, payload, transaction_url)
        client.request(commit_url)
    except Exception:
        try:
            client.request(transaction_url, method='DELETE')
        except Exception:
            pass
        raise
    result = verify_graph(client, release_id, payload)
    result['database_nodes_before'] = before_count
    return result


def persist_snapshot(connection, release_id):
    directory = ROOT / 'graph_staging'
    require(not directory.is_symlink(), 'Snapshot directory must not be a symlink')
    directory.mkdir(exist_ok=True)
    destination = directory / (release_id + '.sqlite')
    if destination.exists() or destination.is_symlink():
        require(not destination.is_symlink(), 'Snapshot must not be a symlink')
        with sqlite3.connect(destination.as_uri() + '?mode=ro', uri=True) as previous:
            release = previous.execute('SELECT created_at FROM data_release WHERE release_id = ?', (release_id,)).fetchone()
            require(release is not None, 'Existing snapshot has no matching release')
            connection.execute('UPDATE data_release SET created_at = ? WHERE release_id = ?', (release[0], release_id))
            connection.commit()
            require('\n'.join(previous.iterdump()) == '\n'.join(connection.iterdump()), 'Existing snapshot differs; it was not overwritten')
    else:
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        target = sqlite3.connect(destination)
        try:
            connection.backup(target)
        finally:
            target.close()
    return destination


def main():
    parser = argparse.ArgumentParser(description='Load only public university source evidence into local admissions2028.')
    parser.add_argument('--plan', action='store_true', help='Validate sources and build an in-memory SQLite plan; no Neo4j connection.')
    parser.add_argument('--verify-only', action='store_true', help='Authenticate and verify the existing release without writes.')
    parser.add_argument('--username', default='neo4j')
    arguments = parser.parse_args()
    connection, release_id, payload, page_total = build_snapshot()
    try:
        summary = {'release_id': release_id, 'pdf_pages': page_total, **expected_counts(payload)}
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        if arguments.plan:
            print('PLAN VERIFIED: no files or databases were modified.')
            return
        require(sys.stdin.isatty(), 'Run this single command directly in an interactive terminal for hidden password input.')
        warnings.simplefilter('error', getpass.GetPassWarning)
        password = getpass.getpass('Neo4j password (hidden; not saved): ')
        require(bool(password), 'No password entered')
        client = Neo4jClient(arguments.username, password)
        del password
        preflight(client)
        if arguments.verify_only:
            print(json.dumps(verify_graph(client, release_id, payload), indent=2))
            return
        snapshot = persist_snapshot(connection, release_id)
        result = apply_graph(client, release_id, payload)
        receipt = {'status': 'verified', 'database': 'admissions2028', 'release_id': release_id, 'review_status': 'draft', 'verified_at': datetime.now(timezone.utc).isoformat(), 'sqlite_snapshot': snapshot.name, **result}
        receipt_path = snapshot.with_suffix('.receipt.json')
        require(not receipt_path.is_symlink(), 'Receipt must not be a symlink')
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('LOAD VERIFIED: ' + str(receipt_path), flush=True)
    finally:
        connection.close()


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print('STOPPED: ' + str(error), file=sys.stderr)
        sys.exit(1)