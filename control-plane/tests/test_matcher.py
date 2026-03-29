from app.matcher import build_context, evaluate_rules

RULES = [
    {
        "id": "rule-1",
        "name": "Block large claude calls",
        "priority": 10,
        "match_logic": {">": [{"var": "total_chars"}, 10000]},
        "mutate_logic": {"strip_tag": [{"var": "messages"}, "<system-reminder>"]},
    },
    {
        "id": "rule-2",
        "name": "Flag bash tool usage",
        "priority": 20,
        "match_logic": {"in": ["Bash", {"var": "tool_names"}]},
        "mutate_logic": {"truncate_after": [{"var": "messages"}, 5000]},
    },
]


def test_build_context_extracts_fields():
    payload = {
        "model": "claude-sonnet-4-6",
        "tools": [{"name": "Bash"}, {"name": "Read"}],
        "messages": [{"role": "user", "content": "hello"}],
        "system": "you are helpful",
    }
    ctx = build_context(payload)
    assert ctx["model"] == "claude-sonnet-4-6"
    assert "Bash" in ctx["tool_names"]
    assert ctx["has_system"] is True
    assert ctx["message_count"] == 1


def test_evaluate_rules_returns_matched_in_priority_order():
    payload = {
        "model": "claude-sonnet-4-6",
        "tools": [{"name": "Bash"}],
        "messages": [{"role": "user", "content": "x" * 20000}],
    }
    matched = evaluate_rules(RULES, payload)
    assert len(matched) == 2
    assert matched[0]["id"] == "rule-1"
    assert matched[1]["id"] == "rule-2"


def test_evaluate_rules_returns_empty_when_no_match():
    payload = {
        "model": "claude-haiku",
        "tools": [],
        "messages": [{"role": "user", "content": "hi"}],
    }
    matched = evaluate_rules(RULES, payload)
    assert matched == []
