When writing or modifying tests:
- Always mock LLM calls — never make real Ollama requests in tests
- Use `@patch` from unittest.mock to mock `ChatOllama`
- Create fixtures as JSON files in `tests/fixtures/` for reusable test data
- Test the function logic, not the model's output quality
- Every test file needs a docstring explaining what module it covers
- Use `pytest.mark.parametrize` for testing multiple diff inputs against the parser
- Name tests descriptively: `test_quality_agent_returns_empty_on_clean_diff`
