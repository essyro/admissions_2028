PRAGMA foreign_keys = ON;
BEGIN;

CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
INSERT INTO schema_version(version) VALUES (1);

CREATE TABLE university (
    university_id TEXT PRIMARY KEY,
    name TEXT NOT NULL
) STRICT;

INSERT INTO university VALUES
    ('KU', '건국대학교'), ('KHU', '경희대학교'), ('PNU', '부산대학교'),
    ('SNU', '서울대학교'), ('YSU', '연세대학교'), ('CAU', '중앙대학교');

CREATE TABLE counseling_university (
    university_id TEXT PRIMARY KEY REFERENCES university
) STRICT;
INSERT INTO counseling_university SELECT university_id FROM university;

CREATE TABLE campus (
    campus_id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL REFERENCES university,
    name TEXT NOT NULL,
    UNIQUE(campus_id, university_id)
) STRICT;

CREATE TABLE curriculum (
    curriculum_code TEXT PRIMARY KEY,
    name TEXT NOT NULL
) STRICT;
INSERT INTO curriculum VALUES ('2015', '2015 개정 교육과정'), ('2022', '2022 개정 교육과정');

CREATE TABLE course (
    course_id TEXT PRIMARY KEY,
    curriculum_code TEXT NOT NULL REFERENCES curriculum,
    subject_group TEXT NOT NULL,
    official_name TEXT NOT NULL,
    elective_type TEXT NOT NULL CHECK(elective_type IN ('공통','일반선택','진로선택','융합선택','전문교과','기타','미확인')),
    UNIQUE(curriculum_code, subject_group, official_name),
    UNIQUE(course_id, curriculum_code)
) STRICT;

CREATE TABLE course_alias (
    course_id TEXT NOT NULL REFERENCES course,
    alias_text TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'draft' CHECK(review_status IN ('draft','reviewed','conflict')),
    PRIMARY KEY(course_id, alias_text)
) STRICT;

CREATE TABLE data_release (
    release_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approval_status TEXT NOT NULL DEFAULT 'draft' CHECK(approval_status IN ('draft','approved','retired'))
) STRICT;

CREATE TABLE source_document (
    document_id TEXT PRIMARY KEY,
    university_id TEXT REFERENCES university,
    document_type TEXT NOT NULL CHECK(document_type IN ('student_guide','admission_plan','course_guide','final_guide','admission_result','other')),
    title TEXT NOT NULL
) STRICT;

CREATE TABLE source_document_version (
    document_version_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES source_document,
    file_sha256 TEXT NOT NULL UNIQUE CHECK(length(file_sha256) = 64 AND file_sha256 NOT GLOB '*[^0-9a-f]*'),
    local_path TEXT NOT NULL,
    publication_date TEXT,
    revision_label TEXT,
    extraction_method TEXT,
    review_status TEXT NOT NULL DEFAULT 'draft' CHECK(review_status IN ('draft','reviewed','conflict'))
) STRICT;

CREATE TABLE source_fragment (
    fragment_id TEXT PRIMARY KEY,
    document_version_id TEXT NOT NULL REFERENCES source_document_version,
    pdf_page INTEGER NOT NULL CHECK(pdf_page > 0),
    printed_page TEXT,
    table_key TEXT,
    row_key TEXT,
    quote TEXT NOT NULL CHECK(length(trim(quote)) > 0)
) STRICT;

CREATE TABLE evidence_set (
    evidence_set_id TEXT PRIMARY KEY,
    review_status TEXT NOT NULL DEFAULT 'draft' CHECK(review_status IN ('draft','reviewed','conflict'))
) STRICT;

CREATE TABLE evidence_set_item (
    evidence_set_id TEXT NOT NULL REFERENCES evidence_set,
    fragment_id TEXT NOT NULL REFERENCES source_fragment,
    PRIMARY KEY(evidence_set_id, fragment_id)
) STRICT;

CREATE VIEW v_reviewed_evidence AS
SELECT evidence_set_id FROM evidence_set AS evidence
WHERE evidence.review_status = 'reviewed'
  AND EXISTS (SELECT 1 FROM evidence_set_item WHERE evidence_set_id = evidence.evidence_set_id)
  AND NOT EXISTS (
      SELECT 1 FROM evidence_set_item AS item
      JOIN source_fragment AS fragment USING(fragment_id)
      JOIN source_document_version AS document USING(document_version_id)
      WHERE item.evidence_set_id = evidence.evidence_set_id AND document.review_status != 'reviewed'
  );

CREATE TABLE academic_unit (
    unit_id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL REFERENCES university,
    campus_id TEXT,
    unit_kind TEXT NOT NULL CHECK(unit_kind IN ('college','school','department','major','open_major','other')),
    FOREIGN KEY(campus_id, university_id) REFERENCES campus(campus_id, university_id),
    UNIQUE(unit_id, university_id)
) STRICT;

CREATE TABLE academic_unit_version (
    unit_version_id TEXT PRIMARY KEY,
    unit_id TEXT NOT NULL,
    university_id TEXT NOT NULL,
    admission_year INTEGER NOT NULL CHECK(admission_year BETWEEN 2000 AND 2100),
    release_id TEXT NOT NULL REFERENCES data_release,
    official_name TEXT NOT NULL,
    field_name TEXT,
    field_basis TEXT NOT NULL DEFAULT 'unknown' CHECK(field_basis IN ('source','reviewed_mapping','inferred','unknown')),
    evidence_set_id TEXT NOT NULL REFERENCES evidence_set,
    review_status TEXT NOT NULL DEFAULT 'draft' CHECK(review_status IN ('draft','reviewed','conflict')),
    FOREIGN KEY(unit_id, university_id) REFERENCES academic_unit(unit_id, university_id),
    UNIQUE(unit_id, admission_year, release_id),
    UNIQUE(unit_version_id, university_id, admission_year, release_id)
) STRICT;

CREATE TABLE admission_track (
    track_id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL REFERENCES university,
    UNIQUE(track_id, university_id)
) STRICT;

CREATE TABLE admission_track_version (
    track_version_id TEXT PRIMARY KEY,
    track_id TEXT NOT NULL,
    university_id TEXT NOT NULL,
    admission_year INTEGER NOT NULL CHECK(admission_year BETWEEN 2000 AND 2100),
    release_id TEXT NOT NULL REFERENCES data_release,
    official_name TEXT NOT NULL,
    admission_type TEXT NOT NULL CHECK(admission_type IN ('학생부교과','학생부종합','논술','실기/실적','특기자','수능','기타','미확인')),
    selection_type TEXT,
    round TEXT NOT NULL CHECK(round IN ('수시','정시','추가','미확인')),
    evidence_set_id TEXT NOT NULL REFERENCES evidence_set,
    review_status TEXT NOT NULL DEFAULT 'draft' CHECK(review_status IN ('draft','reviewed','conflict')),
    FOREIGN KEY(track_id, university_id) REFERENCES admission_track(track_id, university_id),
    UNIQUE(track_id, admission_year, release_id),
    UNIQUE(track_version_id, university_id, admission_year, release_id)
) STRICT;

CREATE TABLE admission_offering (
    offering_id TEXT PRIMARY KEY,
    track_version_id TEXT NOT NULL,
    unit_version_id TEXT NOT NULL,
    university_id TEXT NOT NULL,
    admission_year INTEGER NOT NULL,
    release_id TEXT NOT NULL,
    quota_category TEXT NOT NULL CHECK(quota_category IN ('정원내','정원외','미기재')),
    evidence_set_id TEXT NOT NULL REFERENCES evidence_set,
    review_status TEXT NOT NULL DEFAULT 'draft' CHECK(review_status IN ('draft','reviewed','conflict')),
    FOREIGN KEY(track_version_id, university_id, admission_year, release_id)
        REFERENCES admission_track_version(track_version_id, university_id, admission_year, release_id),
    FOREIGN KEY(unit_version_id, university_id, admission_year, release_id)
        REFERENCES academic_unit_version(unit_version_id, university_id, admission_year, release_id),
    UNIQUE(track_version_id, unit_version_id, quota_category),
    UNIQUE(offering_id, university_id, admission_year, release_id)
) STRICT;

CREATE TABLE quota_fact (
    quota_id TEXT PRIMARY KEY,
    offering_id TEXT NOT NULL REFERENCES admission_offering,
    quota_kind TEXT NOT NULL CHECK(quota_kind IN ('plan','revised_plan','result')),
    count INTEGER CHECK(count >= 0),
    value_status TEXT NOT NULL CHECK(value_status IN ('known','not_published','not_disclosed','not_applicable','unknown')),
    evidence_set_id TEXT NOT NULL REFERENCES evidence_set,
    CHECK((value_status = 'known' AND count IS NOT NULL) OR (value_status != 'known' AND count IS NULL)),
    UNIQUE(offering_id, quota_kind)
) STRICT;

CREATE TABLE policy_scope (
    scope_id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL REFERENCES university,
    admission_year INTEGER NOT NULL CHECK(admission_year BETWEEN 2000 AND 2100),
    release_id TEXT NOT NULL REFERENCES data_release,
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('university','unit','track','offering')),
    scope_text TEXT NOT NULL,
    evidence_set_id TEXT NOT NULL REFERENCES evidence_set,
    UNIQUE(scope_id, university_id, admission_year, release_id, scope_kind)
) STRICT;

CREATE TABLE scope_unit (
    scope_id TEXT NOT NULL,
    unit_version_id TEXT NOT NULL,
    university_id TEXT NOT NULL,
    admission_year INTEGER NOT NULL,
    release_id TEXT NOT NULL,
    scope_kind TEXT NOT NULL DEFAULT 'unit' CHECK(scope_kind = 'unit'),
    PRIMARY KEY(scope_id, unit_version_id),
    FOREIGN KEY(scope_id, university_id, admission_year, release_id, scope_kind)
        REFERENCES policy_scope(scope_id, university_id, admission_year, release_id, scope_kind),
    FOREIGN KEY(unit_version_id, university_id, admission_year, release_id)
        REFERENCES academic_unit_version(unit_version_id, university_id, admission_year, release_id)
) STRICT;

CREATE TABLE scope_track (
    scope_id TEXT NOT NULL,
    track_version_id TEXT NOT NULL,
    university_id TEXT NOT NULL,
    admission_year INTEGER NOT NULL,
    release_id TEXT NOT NULL,
    scope_kind TEXT NOT NULL DEFAULT 'track' CHECK(scope_kind = 'track'),
    PRIMARY KEY(scope_id, track_version_id),
    FOREIGN KEY(scope_id, university_id, admission_year, release_id, scope_kind)
        REFERENCES policy_scope(scope_id, university_id, admission_year, release_id, scope_kind),
    FOREIGN KEY(track_version_id, university_id, admission_year, release_id)
        REFERENCES admission_track_version(track_version_id, university_id, admission_year, release_id)
) STRICT;

CREATE TABLE scope_offering (
    scope_id TEXT NOT NULL,
    offering_id TEXT NOT NULL,
    university_id TEXT NOT NULL,
    admission_year INTEGER NOT NULL,
    release_id TEXT NOT NULL,
    scope_kind TEXT NOT NULL DEFAULT 'offering' CHECK(scope_kind = 'offering'),
    PRIMARY KEY(scope_id, offering_id),
    FOREIGN KEY(scope_id, university_id, admission_year, release_id, scope_kind)
        REFERENCES policy_scope(scope_id, university_id, admission_year, release_id, scope_kind),
    FOREIGN KEY(offering_id, university_id, admission_year, release_id)
        REFERENCES admission_offering(offering_id, university_id, admission_year, release_id)
) STRICT;

CREATE TABLE policy_rule (
    rule_id TEXT PRIMARY KEY,
    rule_kind TEXT NOT NULL CHECK(rule_kind IN ('eligibility','csat_minimum','score_formula','duplicate_application','course_selection','stage_selection')),
    rule_text TEXT NOT NULL,
    executable_status TEXT NOT NULL DEFAULT 'untranslated' CHECK(executable_status IN ('untranslated','draft','verified')),
    evidence_set_id TEXT NOT NULL REFERENCES evidence_set
) STRICT;

CREATE TABLE rule_scope (
    rule_id TEXT NOT NULL REFERENCES policy_rule,
    scope_id TEXT NOT NULL REFERENCES policy_scope,
    application_kind TEXT NOT NULL CHECK(application_kind IN ('applies','exception','replaces')),
    supersedes_rule_id TEXT REFERENCES policy_rule,
    PRIMARY KEY(rule_id, scope_id),
    CHECK(rule_id IS NOT supersedes_rule_id),
    CHECK(application_kind != 'replaces' OR supersedes_rule_id IS NOT NULL)
) STRICT;

CREATE TABLE rule_expression (
    expression_id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL REFERENCES policy_rule,
    parent_expression_id TEXT,
    operator TEXT NOT NULL CHECK(operator IN ('AND','OR','AT_LEAST','COMPARE','COMPLETED','GRADE_SUM','SUBJECT_SELECTION')),
    operand_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(operand_json) AND json_type(operand_json) = 'object'),
    UNIQUE(expression_id, rule_id),
    FOREIGN KEY(parent_expression_id, rule_id) REFERENCES rule_expression(expression_id, rule_id),
    CHECK(expression_id IS NOT parent_expression_id)
) STRICT;
CREATE UNIQUE INDEX one_rule_root ON rule_expression(rule_id) WHERE parent_expression_id IS NULL;

CREATE TABLE course_recommendation (
    recommendation_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES policy_scope,
    curriculum_code TEXT NOT NULL REFERENCES curriculum,
    recommendation_level TEXT NOT NULL CHECK(recommendation_level IN ('핵심권장','권장','우선이수','미지정')),
    rule_id TEXT REFERENCES policy_rule,
    condition_text TEXT NOT NULL,
    evidence_set_id TEXT NOT NULL REFERENCES evidence_set,
    UNIQUE(recommendation_id, curriculum_code)
) STRICT;

CREATE TABLE recommendation_course (
    recommendation_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    curriculum_code TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('candidate','included')),
    PRIMARY KEY(recommendation_id, course_id),
    FOREIGN KEY(recommendation_id, curriculum_code) REFERENCES course_recommendation(recommendation_id, curriculum_code),
    FOREIGN KEY(course_id, curriculum_code) REFERENCES course(course_id, curriculum_code)
) STRICT;

CREATE TABLE selection_stage (
    stage_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES policy_scope,
    stage_order INTEGER NOT NULL CHECK(stage_order > 0),
    pass_count_rule_id TEXT REFERENCES policy_rule,
    evidence_set_id TEXT NOT NULL REFERENCES evidence_set,
    UNIQUE(scope_id, stage_order)
) STRICT;

CREATE TABLE evaluation_component (
    component_id TEXT PRIMARY KEY,
    stage_id TEXT NOT NULL REFERENCES selection_stage,
    component_type TEXT NOT NULL CHECK(component_type IN ('교과','서류','면접','수능','실기','논술','이전단계','출결','기타')),
    weight REAL CHECK(weight BETWEEN 0 AND 100),
    max_score REAL CHECK(max_score > 0),
    score_rule_id TEXT REFERENCES policy_rule,
    evidence_set_id TEXT NOT NULL REFERENCES evidence_set
) STRICT;

CREATE TABLE admission_event (
    event_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES policy_scope,
    event_type TEXT NOT NULL,
    start_at TEXT,
    end_at TEXT,
    timezone TEXT NOT NULL DEFAULT 'Asia/Seoul',
    value_status TEXT NOT NULL CHECK(value_status IN ('known','not_published','not_applicable','unknown')),
    evidence_set_id TEXT NOT NULL REFERENCES evidence_set,
    CHECK((value_status = 'known' AND start_at IS NOT NULL) OR (value_status != 'known' AND start_at IS NULL AND end_at IS NULL)),
    CHECK(end_at IS NULL OR (start_at IS NOT NULL AND end_at >= start_at))
) STRICT;

CREATE TABLE evaluation_item (
    item_id TEXT PRIMARY KEY,
    parent_item_id TEXT REFERENCES evaluation_item,
    name TEXT NOT NULL,
    CHECK(item_id IS NOT parent_item_id)
) STRICT;

CREATE TABLE evaluation_guidance (
    guidance_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES policy_scope,
    item_id TEXT NOT NULL REFERENCES evaluation_item,
    perspective_text TEXT NOT NULL,
    emphasis TEXT,
    evidence_set_id TEXT NOT NULL REFERENCES evidence_set
) STRICT;

CREATE TABLE admission_characteristic (
    characteristic_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES policy_scope,
    topic TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence_set_id TEXT NOT NULL REFERENCES evidence_set
) STRICT;

CREATE TABLE duplicate_application_rule (
    rule_id TEXT NOT NULL REFERENCES policy_rule,
    first_scope_id TEXT NOT NULL REFERENCES policy_scope,
    second_scope_id TEXT NOT NULL REFERENCES policy_scope,
    relation_kind TEXT NOT NULL CHECK(relation_kind IN ('allowed','prohibited','conditional','unknown')),
    PRIMARY KEY(rule_id, first_scope_id, second_scope_id)
) STRICT;

CREATE VIEW v_student_record_candidates AS
SELECT offering.offering_id, offering.release_id, offering.admission_year,
       offering.university_id, university.name AS university_name,
       track.official_name AS track_name, track.admission_type,
       unit.official_name AS unit_name, unit.field_name, unit.field_basis,
       offering.quota_category, offering.evidence_set_id
FROM admission_offering AS offering
JOIN university USING(university_id)
JOIN counseling_university USING(university_id)
JOIN admission_track_version AS track USING(track_version_id)
JOIN academic_unit_version AS unit USING(unit_version_id)
JOIN data_release AS release ON release.release_id = offering.release_id
WHERE offering.admission_year IN (2027, 2028)
  AND track.round = '수시' AND track.admission_type IN ('학생부교과','학생부종합')
  AND release.approval_status = 'approved'
  AND offering.review_status = 'reviewed' AND track.review_status = 'reviewed' AND unit.review_status = 'reviewed'
  AND offering.evidence_set_id IN (SELECT evidence_set_id FROM v_reviewed_evidence)
  AND track.evidence_set_id IN (SELECT evidence_set_id FROM v_reviewed_evidence)
  AND unit.evidence_set_id IN (SELECT evidence_set_id FROM v_reviewed_evidence);

CREATE INDEX offering_year_type_lookup ON admission_offering(admission_year, university_id, release_id);
COMMIT;