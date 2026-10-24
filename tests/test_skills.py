from app.skills import matching_skills, public_skills, skill_context


def test_public_skill_catalog_has_expected_profiles():
    ids = {item['id'] for item in public_skills()}
    assert ids == {
        'strategic_operator', 'software_engineering', 'research_and_evidence',
        'rz_principles', 'learning_coach', 'ethical_tool_use',
        'creator_and_communication',
    }


def test_software_engineering_skill_matches_debugging_request():
    matches = matching_skills('Help debug this Python API error in my repo')
    assert matches
    assert matches[0]['id'] == 'software_engineering'


def test_rz_principles_skill_matches_relationship_reflection():
    ids = {item['id'] for item in matching_skills('How should I think about loyalty and boundaries?')}
    assert 'rz_principles' in ids


def test_skill_context_preserves_approval_boundaries():
    context = skill_context('automate this task')
    assert 'approval boundaries' in context.lower()
    assert 'do not grant permissions' in context.lower()
