from app.intent_router import should_use_agent_tools


def test_explicit_agent_request_uses_agent_loop():
    assert should_use_agent_tools('Please use your tools to investigate this issue')


def test_natural_multistep_research_uses_agent_loop():
    assert should_use_agent_tools('Research and compare the options, then summarize your recommendation')


def test_resume_existing_work_uses_agent_loop():
    assert should_use_agent_tools('Continue where we left off on the project')


def test_memory_request_uses_agent_loop():
    assert should_use_agent_tools('Please remember this for later: I prefer short summaries')


def test_simple_chat_does_not_force_agent_loop():
    assert not should_use_agent_tools('What is a PWA?')
    assert not should_use_agent_tools('Tell me a joke')
    assert not should_use_agent_tools('I like learning about AI agents')


def test_empty_message_does_not_force_agent_loop():
    assert not should_use_agent_tools('  ')


def test_basic_github_repository_lookup_keeps_direct_lookup_route():
    # A straightforward repository check should not be diverted into the broad agent loop.
    assert not should_use_agent_tools('Check my GitHub repository rz-ai-agent')
    assert should_use_agent_tools('Review this repo and report the key issues')


def test_investment_research_routes_to_agent_tools():
    assert should_use_agent_tools('Find investment opportunities in ETFs')
    assert should_use_agent_tools('Compare stocks for the best buying opportunity')
    assert should_use_agent_tools('Research this stock and give me an entry price')


def test_opportunity_scanner_routes_to_agent_tools():
    assert should_use_agent_tools("Run the opportunity scanner on these ETFs")
    assert should_use_agent_tools("Scan investment opportunities in technology stocks")


def test_explicit_investment_tool_request_routes_to_agent_tools():
    assert should_use_agent_tools(
        'Run mia_opportunity_scan for 3 U.S.-listed stocks. Use live quotes and source-backed research.'
    )
    assert should_use_agent_tools(
        'Return candidates, quote timestamps, source links, key risks, and evidence quality.'
    )
