PRAGMA foreign_keys = ON;
BEGIN;

CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
INSERT INTO schema_version(version) VALUES (1);

CREATE TABLE student_profile (
    profile_id TEXT PRIMARY KEY,
    pseudonym TEXT NOT NULL CHECK(length(trim(pseudonym)) > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

CREATE TABLE profile_revision (
    revision_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES student_profile ON DELETE CASCADE,
    revision_number INTEGER NOT NULL CHECK(revision_number > 0),
    admission_year INTEGER NOT NULL CHECK(admission_year IN (2027, 2028)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(profile_id, revision_number),
    UNIQUE(revision_id, profile_id)
) STRICT;

CREATE TABLE student_term (
    term_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES profile_revision ON DELETE CASCADE,
    school_year INTEGER NOT NULL CHECK(school_year BETWEEN 2000 AND 2100),
    school_grade INTEGER NOT NULL CHECK(school_grade BETWEEN 1 AND 3),
    semester INTEGER NOT NULL CHECK(semester IN (1, 2)),
    UNIQUE(revision_id, school_grade, semester),
    UNIQUE(term_id, revision_id)
) STRICT;

CREATE TABLE student_import_batch (
    batch_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES profile_revision ON DELETE CASCADE,
    input_kind TEXT NOT NULL CHECK(input_kind IN ('manual','csv','xlsx')),
    content_sha256 TEXT CHECK(content_sha256 IS NULL OR (length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*')),
    import_status TEXT NOT NULL CHECK(import_status IN ('pending','validated','rejected','applied')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(batch_id, revision_id)
) STRICT;

CREATE TABLE student_import_issue (
    issue_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES student_import_batch ON DELETE CASCADE,
    row_number INTEGER CHECK(row_number >= 2),
    column_name TEXT,
    issue_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('warning','error'))
) STRICT;

CREATE TABLE student_course_record (
    record_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES profile_revision ON DELETE CASCADE,
    term_id TEXT NOT NULL,
    batch_id TEXT,
    source_row INTEGER CHECK(source_row >= 2),
    course_name TEXT NOT NULL CHECK(length(trim(course_name)) > 0),
    subject_group TEXT,
    curriculum_code TEXT NOT NULL CHECK(curriculum_code IN ('2015','2022','unknown')),
    elective_type TEXT NOT NULL CHECK(elective_type IN ('공통','일반선택','진로선택','융합선택','전문교과','기타','미확인')),
    course_status TEXT NOT NULL CHECK(course_status IN ('completed','in_progress','planned')),
    credits REAL CHECK(credits > 0),
    grade_system TEXT NOT NULL CHECK(grade_system IN ('5','9','achievement_only','unknown')),
    rank_grade INTEGER,
    achievement TEXT CHECK(achievement IN ('A','B','C','D','E','P','F')),
    resolved_course_id TEXT,
    resolved_release_id TEXT,
    FOREIGN KEY(term_id, revision_id) REFERENCES student_term(term_id, revision_id) ON DELETE CASCADE,
    FOREIGN KEY(batch_id, revision_id) REFERENCES student_import_batch(batch_id, revision_id),
    CHECK((grade_system = '5' AND (rank_grade IS NULL OR rank_grade BETWEEN 1 AND 5))
       OR (grade_system = '9' AND (rank_grade IS NULL OR rank_grade BETWEEN 1 AND 9))
       OR (grade_system IN ('achievement_only','unknown') AND rank_grade IS NULL)),
    CHECK(course_status = 'completed' OR (rank_grade IS NULL AND achievement IS NULL)),
    CHECK((resolved_course_id IS NULL AND resolved_release_id IS NULL)
       OR (resolved_course_id IS NOT NULL AND resolved_release_id IS NOT NULL)),
    UNIQUE(term_id, curriculum_code, course_name)
) STRICT;

CREATE TABLE student_course_availability (
    availability_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES profile_revision ON DELETE CASCADE,
    course_name TEXT NOT NULL,
    curriculum_code TEXT NOT NULL CHECK(curriculum_code IN ('2015','2022','unknown')),
    availability TEXT NOT NULL CHECK(availability IN ('available','unavailable','unknown')),
    delivery_mode TEXT CHECK(delivery_mode IN ('school','joint','online','other','unknown'))
) STRICT;

CREATE TABLE student_exam (
    exam_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES profile_revision ON DELETE CASCADE,
    exam_kind TEXT NOT NULL CHECK(exam_kind IN ('csat','mock','scenario')),
    exam_name TEXT NOT NULL,
    exam_date TEXT,
    admission_year INTEGER NOT NULL CHECK(admission_year BETWEEN 2000 AND 2100)
) STRICT;

CREATE TABLE student_exam_score (
    exam_id TEXT NOT NULL REFERENCES student_exam ON DELETE CASCADE,
    subject TEXT NOT NULL,
    grade INTEGER CHECK(grade BETWEEN 1 AND 9),
    standard_score REAL CHECK(standard_score >= 0),
    percentile REAL CHECK(percentile BETWEEN 0 AND 100),
    PRIMARY KEY(exam_id, subject),
    CHECK(grade IS NOT NULL OR standard_score IS NOT NULL OR percentile IS NOT NULL)
) STRICT;

CREATE TABLE student_preference (
    preference_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES profile_revision ON DELETE CASCADE,
    preference_kind TEXT NOT NULL CHECK(preference_kind IN ('university','major','region','cost','other')),
    preference_value TEXT NOT NULL,
    priority INTEGER CHECK(priority > 0)
) STRICT;

CREATE TABLE student_eligibility_fact (
    fact_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES profile_revision ON DELETE CASCADE,
    fact_key TEXT NOT NULL,
    fact_status TEXT NOT NULL CHECK(fact_status IN ('known','unknown')),
    value_json TEXT CHECK(value_json IS NULL OR json_valid(value_json)),
    verification TEXT NOT NULL CHECK(verification IN ('self_reported','counselor_confirmed','unknown')),
    CHECK((fact_status = 'known' AND value_json IS NOT NULL AND value_json != 'null')
       OR (fact_status = 'unknown' AND value_json IS NULL)),
    UNIQUE(revision_id, fact_key)
) STRICT;

CREATE TABLE counseling_session (
    session_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES profile_revision ON DELETE CASCADE,
    admissions_release_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    model_name TEXT,
    rule_engine_version TEXT,
    audience TEXT NOT NULL CHECK(audience IN ('student_parent','counselor'))
) STRICT;

CREATE TABLE counseling_message (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES counseling_session ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    role TEXT NOT NULL CHECK(role IN ('user','assistant','tool')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, sequence)
) STRICT;

CREATE TABLE candidate_assessment (
    assessment_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES counseling_session ON DELETE CASCADE,
    offering_id TEXT NOT NULL,
    eligibility_status TEXT NOT NULL CHECK(eligibility_status IN ('met','not_met','insufficient_information')),
    csat_status TEXT NOT NULL CHECK(csat_status IN ('met','not_met','insufficient_information','not_applicable')),
    csat_basis TEXT NOT NULL CHECK(csat_basis IN ('actual','scenario','not_applicable','unknown')),
    course_fit_summary TEXT,
    rationale TEXT NOT NULL,
    CHECK((csat_basis = 'unknown' AND csat_status = 'insufficient_information')
       OR (csat_basis = 'not_applicable' AND csat_status = 'not_applicable')
       OR (csat_basis IN ('actual','scenario') AND csat_status != 'not_applicable')),
    UNIQUE(session_id, offering_id)
) STRICT;

CREATE TABLE assessment_evidence (
    assessment_id TEXT NOT NULL REFERENCES candidate_assessment ON DELETE CASCADE,
    evidence_set_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    PRIMARY KEY(assessment_id, evidence_set_id, purpose)
) STRICT;

CREATE TABLE assessment_action (
    action_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES candidate_assessment ON DELETE CASCADE,
    action_text TEXT NOT NULL,
    priority INTEGER CHECK(priority > 0),
    due_date TEXT
) STRICT;

CREATE INDEX student_revision_lookup ON profile_revision(profile_id);
CREATE INDEX student_course_revision_lookup ON student_course_record(revision_id);
CREATE INDEX counseling_revision_lookup ON counseling_session(revision_id);
COMMIT;