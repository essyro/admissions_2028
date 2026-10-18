import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import load_public_graph

ROOT = Path(__file__).resolve().parent


def insert(connection: sqlite3.Connection, table: str, **values: object) -> None:
    columns = ', '.join(values)
    placeholders = ', '.join('?' for _ in values)
    connection.execute(f'INSERT INTO {table} ({columns}) VALUES ({placeholders})', tuple(values.values()))


class AdmissionSchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(':memory:')
        self.connection.executescript((ROOT / '01_admissions.sql').read_text())
        insert(self.connection, 'university', university_id='TEST', name='가상 테스트 대학')
        insert(self.connection, 'counseling_university', university_id='TEST')
        insert(self.connection, 'university', university_id='OTHER', name='다른 가상 대학')
        insert(self.connection, 'data_release', release_id='release', approval_status='approved')
        insert(self.connection, 'source_document', document_id='doc', university_id='TEST', document_type='admission_plan', title='합성 검증 자료')
        insert(self.connection, 'source_document_version', document_version_id='doc-v1', document_id='doc', file_sha256='a' * 64, local_path='synthetic.pdf', review_status='reviewed')
        insert(self.connection, 'source_fragment', fragment_id='fragment', document_version_id='doc-v1', pdf_page=1, quote='실제 입학정보가 아닌 스키마 테스트 자료')
        insert(self.connection, 'evidence_set', evidence_set_id='evidence', review_status='reviewed')
        insert(self.connection, 'evidence_set_item', evidence_set_id='evidence', fragment_id='fragment')
        insert(self.connection, 'academic_unit', unit_id='unit', university_id='TEST', unit_kind='department')
        insert(self.connection, 'academic_unit_version', unit_version_id='unit-28', unit_id='unit', university_id='TEST', admission_year=2028, release_id='release', official_name='가상 학과', evidence_set_id='evidence', review_status='reviewed')
        for suffix, kind, year, round_name in [('grade', '학생부교과', 2028, '수시'), ('holistic', '학생부종합', 2028, '수시'), ('essay', '논술', 2028, '수시'), ('regular', '학생부교과', 2028, '정시'), ('old', '학생부교과', 2027, '수시')]:
            insert(self.connection, 'admission_track', track_id=suffix, university_id='TEST')
            insert(self.connection, 'admission_track_version', track_version_id=suffix, track_id=suffix, university_id='TEST', admission_year=year, release_id='release', official_name=suffix, admission_type=kind, round=round_name, evidence_set_id='evidence', review_status='reviewed')
            if year == 2028:
                self.offering(suffix)

    def tearDown(self) -> None:
        self.connection.close()

    def offering(self, track: str, **overrides: object) -> None:
        values = dict(offering_id=track, track_version_id=track, unit_version_id='unit-28', university_id='TEST', admission_year=2028, release_id='release', quota_category='미기재', evidence_set_id='evidence', review_status='reviewed')
        values.update(overrides)
        insert(self.connection, 'admission_offering', **values)

    def candidates(self) -> set[str]:
        return {row[0] for row in self.connection.execute('SELECT offering_id FROM v_student_record_candidates')}

    def test_only_student_record_early_admissions_are_candidates(self) -> None:
        self.assertEqual(self.candidates(), {'grade', 'holistic'})
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM admission_offering').fetchone()[0], 4)

    def test_default_six_university_allowlist(self) -> None:
        self.connection.execute("DELETE FROM counseling_university WHERE university_id='TEST'")
        self.assertEqual(self.candidates(), set())
        self.assertEqual({row[0] for row in self.connection.execute('SELECT university_id FROM counseling_university')}, {'KU', 'KHU', 'PNU', 'SNU', 'YSU', 'CAU'})

    def test_unreviewed_data_is_not_recommended(self) -> None:
        self.connection.execute("UPDATE admission_offering SET review_status='draft' WHERE offering_id='grade'")
        self.assertEqual(self.candidates(), {'holistic'})
        self.connection.execute("UPDATE data_release SET approval_status='draft'")
        self.assertEqual(self.candidates(), set())

    def test_missing_or_conflicting_evidence_blocks_candidates(self) -> None:
        self.connection.execute('DELETE FROM evidence_set_item')
        self.assertEqual(self.candidates(), set())
        insert(self.connection, 'evidence_set_item', evidence_set_id='evidence', fragment_id='fragment')
        self.connection.execute("UPDATE source_document_version SET review_status='conflict'")
        self.assertEqual(self.candidates(), set())

    def test_cross_year_and_university_offering_rejected(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.offering('old')
        with self.assertRaises(sqlite3.IntegrityError):
            self.offering('grade', offering_id='invalid', university_id='OTHER')

    def test_quota_unknown_is_not_zero(self) -> None:
        values = dict(quota_id='quota', offering_id='grade', quota_kind='plan', value_status='unknown', evidence_set_id='evidence')
        with self.assertRaises(sqlite3.IntegrityError):
            insert(self.connection, 'quota_fact', count=0, **values)
        insert(self.connection, 'quota_fact', count=None, **values)
        self.assertIsNone(self.connection.execute('SELECT count FROM quota_fact').fetchone()[0])

    def test_course_curriculum_mismatch_rejected(self) -> None:
        insert(self.connection, 'policy_scope', scope_id='scope', university_id='TEST', admission_year=2028, release_id='release', scope_kind='unit', scope_text='합성 테스트 학과', evidence_set_id='evidence')
        insert(self.connection, 'course_recommendation', recommendation_id='rec', scope_id='scope', curriculum_code='2022', recommendation_level='권장', condition_text='합성 과목 중 선택', evidence_set_id='evidence')
        insert(self.connection, 'course', course_id='old-course', curriculum_code='2015', subject_group='수학', official_name='합성 과목', elective_type='진로선택')
        with self.assertRaises(sqlite3.IntegrityError):
            insert(self.connection, 'recommendation_course', recommendation_id='rec', course_id='old-course', curriculum_code='2022', role='candidate')

    def test_scope_cannot_target_other_year(self) -> None:
        insert(self.connection, 'policy_scope', scope_id='scope', university_id='TEST', admission_year=2027, release_id='release', scope_kind='unit', scope_text='합성 범위', evidence_set_id='evidence')
        with self.assertRaises(sqlite3.IntegrityError):
            insert(self.connection, 'scope_unit', scope_id='scope', unit_version_id='unit-28', university_id='TEST', admission_year=2027, release_id='release')

    def test_rule_tree_cannot_cross_rules(self) -> None:
        for identifier in ['first', 'second']:
            insert(self.connection, 'policy_rule', rule_id=identifier, rule_kind='course_selection', rule_text='합성 조건', evidence_set_id='evidence')
        insert(self.connection, 'rule_expression', expression_id='parent', rule_id='first', operator='AND')
        with self.assertRaises(sqlite3.IntegrityError):
            insert(self.connection, 'rule_expression', expression_id='child', rule_id='second', parent_expression_id='parent', operator='COMPLETED')

    def test_foreign_keys_and_integrity(self) -> None:
        self.assertEqual(self.connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertEqual(self.connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(self.connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')


class StudentSchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(':memory:')
        self.connection.executescript((ROOT / '02_students.sql').read_text())
        insert(self.connection, 'student_profile', profile_id='student', pseudonym='가상 테스트 학생')
        insert(self.connection, 'profile_revision', revision_id='revision', profile_id='student', revision_number=1, admission_year=2028)
        insert(self.connection, 'student_term', term_id='term', revision_id='revision', school_year=2026, school_grade=2, semester=1)

    def tearDown(self) -> None:
        self.connection.close()

    def record(self, **overrides: object) -> None:
        values = dict(record_id='record', revision_id='revision', term_id='term', course_name='합성 과목', curriculum_code='2022', elective_type='일반선택', course_status='completed', grade_system='5', rank_grade=3)
        values.update(overrides)
        insert(self.connection, 'student_course_record', **values)

    def test_five_grade_system_rejects_six(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.record(rank_grade=6)
        self.record(rank_grade=5)

    def test_nine_grade_system_not_inferred_from_admission_year(self) -> None:
        self.record(curriculum_code='2015', grade_system='9', rank_grade=9)
        with self.assertRaises(sqlite3.IntegrityError):
            self.record(record_id='bad', course_name='다른 과목', grade_system='9', rank_grade=10)

    def test_grade_must_be_integral(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.record(rank_grade=2.5)

    def test_achievement_only_and_missing_grade_allowed(self) -> None:
        self.record(grade_system='achievement_only', rank_grade=None, achievement='A')
        self.record(record_id='unknown', course_name='미확인 과목', grade_system='unknown', rank_grade=None)

    def test_planned_and_in_progress_courses_have_no_final_grades(self) -> None:
        for state in ['planned', 'in_progress']:
            with self.subTest(state=state):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.record(course_status=state)
                with self.assertRaises(sqlite3.IntegrityError):
                    self.record(course_status=state, rank_grade=None, achievement='A')
        self.record(course_status='planned', rank_grade=None)

    def test_cross_revision_term_rejected(self) -> None:
        insert(self.connection, 'profile_revision', revision_id='other', profile_id='student', revision_number=2, admission_year=2028)
        with self.assertRaises(sqlite3.IntegrityError):
            self.record(revision_id='other')

    def test_unknown_csat_cannot_be_reported_as_met(self) -> None:
        insert(self.connection, 'counseling_session', session_id='session', revision_id='revision', admissions_release_id='release', audience='counselor')
        with self.assertRaises(sqlite3.IntegrityError):
            insert(self.connection, 'candidate_assessment', assessment_id='assessment', session_id='session', offering_id='offering', eligibility_status='insufficient_information', csat_status='met', csat_basis='unknown', rationale='검증용')

    def test_delete_profile_cascades_to_private_data(self) -> None:
        insert(self.connection, 'student_import_batch', batch_id='batch', revision_id='revision', input_kind='csv', import_status='applied')
        insert(self.connection, 'student_import_issue', issue_id='issue', batch_id='batch', row_number=2, issue_code='TEST', severity='warning')
        self.record(batch_id='batch')
        insert(self.connection, 'student_course_availability', availability_id='availability', revision_id='revision', course_name='가상 과목', curriculum_code='2022', availability='unknown')
        insert(self.connection, 'student_preference', preference_id='preference', revision_id='revision', preference_kind='major', preference_value='가상 전공')
        insert(self.connection, 'student_eligibility_fact', fact_id='fact', revision_id='revision', fact_key='recommendation', fact_status='unknown', verification='unknown')
        insert(self.connection, 'student_exam', exam_id='exam', revision_id='revision', exam_kind='mock', exam_name='합성 모의평가', admission_year=2028)
        insert(self.connection, 'student_exam_score', exam_id='exam', subject='합성 과목', grade=3)
        insert(self.connection, 'counseling_session', session_id='session', revision_id='revision', admissions_release_id='release', audience='student_parent')
        insert(self.connection, 'counseling_message', message_id='message', session_id='session', sequence=1, role='user', content='합성 테스트 메시지')
        insert(self.connection, 'candidate_assessment', assessment_id='assessment', session_id='session', offering_id='offering', eligibility_status='insufficient_information', csat_status='insufficient_information', csat_basis='unknown', rationale='합성 검증 자료')
        insert(self.connection, 'assessment_evidence', assessment_id='assessment', evidence_set_id='evidence', purpose='eligibility')
        insert(self.connection, 'assessment_action', action_id='action', assessment_id='assessment', action_text='추가 확인')
        self.connection.execute("DELETE FROM student_profile WHERE profile_id='student'")
        tables = ['student_profile', 'profile_revision', 'student_term', 'student_import_batch', 'student_import_issue', 'student_course_record', 'student_course_availability', 'student_preference', 'student_eligibility_fact', 'student_exam', 'student_exam_score', 'counseling_session', 'counseling_message', 'candidate_assessment', 'assessment_evidence', 'assessment_action']
        for table in tables:
            with self.subTest(table=table):
                self.assertEqual(self.connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        self.assertEqual(self.connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_csv_template_header(self) -> None:
        with (ROOT / 'student_courses_template.csv').open(encoding='utf-8-sig', newline='') as source:
            rows = list(csv.reader(source))
        self.assertEqual(rows, [['학교연도', '학년', '학기', '과목명', '교과군', '교육과정', '선택유형', '이수상태', '학점', '석차등급', '등급체계', '성취도']])


class StorageBoundaryTest(unittest.TestCase):
    def test_public_and_private_tables_are_separate(self) -> None:
        table_sets = []
        for filename in ['01_admissions.sql', '02_students.sql']:
            connection = sqlite3.connect(':memory:')
            try:
                connection.executescript((ROOT / filename).read_text())
                table_sets.append({row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name != 'schema_version'")})
            finally:
                connection.close()
        public_tables, private_tables = table_sets
        self.assertFalse(public_tables & private_tables)
        self.assertTrue({'student_profile', 'student_course_record', 'counseling_message', 'candidate_assessment'} <= private_tables)
        self.assertTrue({'source_document', 'admission_offering', 'course'} <= public_tables)


class PublicGraphLoaderTest(unittest.TestCase):
    def test_plan_contains_only_draft_public_evidence(self) -> None:
        connection, release_id, payload, page_total = load_public_graph.build_snapshot()
        try:
            self.assertEqual(load_public_graph.expected_counts(payload), {'nodes': {'University': 6, 'DocumentVersion': 17, 'EvidenceFragment': 505}, 'relationships': {'HAS_DOCUMENT': 17, 'IN_DOCUMENT': 505}})
            self.assertEqual(page_total, 581)
            self.assertTrue(release_id.startswith(load_public_graph.OWNER))
            self.assertTrue(all(record['review_status'] == 'draft' for records in payload.values() for record in records))
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM v_student_record_candidates').fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_schema WHERE name='student_profile'").fetchone()[0], 0)
        finally:
            connection.close()

    def test_snapshot_reuse_preserves_original_creation_time(self) -> None:
        connection, release_id, _, _ = load_public_graph.build_snapshot()
        try:
            connection.execute("UPDATE data_release SET created_at='2026-01-01 00:00:00'")
            connection.commit()
            with tempfile.TemporaryDirectory() as directory, patch.object(load_public_graph, 'ROOT', Path(directory)):
                first = load_public_graph.persist_snapshot(connection, release_id)
                original_bytes = first.read_bytes()
                connection.execute("UPDATE data_release SET created_at='2026-02-01 00:00:00'")
                connection.commit()
                second = load_public_graph.persist_snapshot(connection, release_id)
                self.assertEqual(first, second)
                self.assertEqual(second.read_bytes(), original_bytes)
                self.assertEqual(connection.execute('SELECT created_at FROM data_release').fetchone()[0], '2026-01-01 00:00:00')
        finally:
            connection.close()

    def test_foreign_namespace_blocks_before_writes(self) -> None:
        client = MagicMock()
        client.query.side_effect = [
            {'results': [{'columns': ['name'], 'data': [{'row': ['admissions2028']}]}]},
            {'results': [{'columns': ['count'], 'data': [{'row': [1]}]}]},
        ]
        with self.assertRaisesRegex(RuntimeError, 'another source'):
            load_public_graph.preflight(client)
        self.assertEqual(client.query.call_count, 2)
        self.assertTrue(all('CREATE' not in call.args[0] for call in client.query.call_args_list))

    def test_wrong_database_blocks_before_writes(self) -> None:
        client = MagicMock()
        client.query.return_value = {'results': [{'columns': ['name'], 'data': [{'row': ['neo4j']}]}]}
        with self.assertRaisesRegex(RuntimeError, 'Wrong target'):
            load_public_graph.preflight(client)
        self.assertEqual(client.query.call_count, 1)

    def test_merge_count_mismatch_rolls_back(self) -> None:
        client = MagicMock()
        transaction_url = load_public_graph.TX_BASE + '/123'
        client.request.return_value = {'commit': transaction_url + '/commit'}
        client.query.return_value = {'results': [{'columns': ['matched'], 'data': [{'row': [0]}]}]}
        with patch.object(load_public_graph, 'preflight', return_value=0):
            with self.assertRaisesRegex(RuntimeError, 'Node count mismatch'):
                load_public_graph.apply_graph(client, 'test-release', {'University': [{'admissions_id': 'university:TEST'}]})
        client.request.assert_any_call(transaction_url, method='DELETE')
        self.assertFalse(any(call.args[0] == transaction_url + '/commit' for call in client.request.call_args_list))

    def test_client_rejects_remote_transaction_url(self) -> None:
        client = load_public_graph.Neo4jClient('synthetic-user', 'synthetic-password')
        client.opener = MagicMock()
        with self.assertRaisesRegex(RuntimeError, 'Unexpected transaction URL'):
            client.request('https://example.com/collect')
        client.opener.open.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)