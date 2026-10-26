from app.skills import matching_skills, public_skills, skill_context


def test_public_skill_catalog_has_expected_profiles():
    ids = {item['id'] for item in public_skills()}
    assert ids == {
        'strategic_operator', 'software_engineering', 'machine_learning_engineering', 'investment_intelligence',
        'research_and_evidence', 'rz_principles', 'learning_coach',
        'ethical_tool_use', 'creator_and_communication',
    }


def test_investment_skill_matches_market_and_entry_price_questions():
    matches = matching_skills('Compare ETFs and stocks for the best buying opportunity and entry price')
    assert matches
    assert matches[0]['id'] == 'investment_intelligence'
    assert 'do not invent prices' in matches[0]['guidance']
    assert 'Do not execute trades' in matches[0]['guidance']


def test_investment_context_mentions_risk_and_uncertainty():
    context = skill_context('Find investment opportunities in the market')
    assert 'downside risks' in context
    assert 'live data is unavailable' in context


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
