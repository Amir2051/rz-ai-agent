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



def test_broad_investment_scan_requires_candidate_discovery():
    from app.intent_router import requires_investment_candidate_discovery
    assert requires_investment_candidate_discovery(
        "Run mia_opportunity_scan for 3 U.S.-listed stocks with live quotes."
    )
    assert requires_investment_candidate_discovery("Find investment opportunities in healthcare ETFs")


def test_named_investment_candidates_skip_discovery_stage():
    from app.intent_router import requires_investment_candidate_discovery
    assert not requires_investment_candidate_discovery("Compare AAPL and MSFT with current quotes")



def test_explicit_ticker_extraction_finds_requested_scan_symbols():
    from app.intent_router import extract_explicit_investment_tickers
    assert extract_explicit_investment_tickers(
        "Compare TSLA, AMZN, and NVDA with current quotes and source-backed research"
    ) == ["NVDA", "TSLA", "AMZN"] or set(extract_explicit_investment_tickers(
        "Compare TSLA, AMZN, and NVDA with current quotes and source-backed research"
    )) == {"TSLA", "AMZN", "NVDA"}


def test_explicit_ticker_extraction_ignores_ticker_substrings():
    from app.intent_router import extract_explicit_investment_tickers
    assert extract_explicit_investment_tickers("The answer is not aaplastic example") == []
