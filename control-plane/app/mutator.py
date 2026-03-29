import re
import copy


def _apply_strip_tag(messages: list[dict], tag: str) -> list[dict]:
    # tag may be "<system-reminder>" or just "system-reminder"
    tag_name = tag.strip("<>")
    pattern = re.compile(
        rf"<{re.escape(tag_name)}[^>]*>.*?</{re.escape(tag_name)}>", re.DOTALL
    )
    result = []
    for msg in messages:
        new_msg = dict(msg)
        if isinstance(new_msg.get("content"), str):
            new_msg["content"] = pattern.sub("", new_msg["content"]).strip()
        result.append(new_msg)
    return result


def _apply_truncate_after(messages: list[dict], max_chars: int) -> list[dict]:
    result = []
    for msg in messages:
        new_msg = dict(msg)
        if isinstance(new_msg.get("content"), str):
            new_msg["content"] = new_msg["content"][:max_chars]
        result.append(new_msg)
    return result


def _apply_regex_delete(messages: list[dict], pattern: str) -> list[dict]:
    compiled = re.compile(pattern, re.DOTALL)
    result = []
    for msg in messages:
        new_msg = dict(msg)
        if isinstance(new_msg.get("content"), str):
            new_msg["content"] = compiled.sub("", new_msg["content"]).strip()
        result.append(new_msg)
    return result


def _apply_replace(messages: list[dict], find: str, replacement: str) -> list[dict]:
    result = []
    for msg in messages:
        new_msg = dict(msg)
        if isinstance(new_msg.get("content"), str):
            new_msg["content"] = new_msg["content"].replace(find, replacement)
        result.append(new_msg)
    return result


def _execute_mutate_logic(payload: dict, logic: dict) -> dict:
    payload = copy.deepcopy(payload)
    messages = payload.get("messages", [])

    if "strip_tag" in logic:
        tag = logic["strip_tag"][0]
        payload["messages"] = _apply_strip_tag(messages, tag)
    elif "truncate_after" in logic:
        max_chars = logic["truncate_after"][0]
        payload["messages"] = _apply_truncate_after(messages, max_chars)
    elif "regex_delete" in logic:
        pattern = logic["regex_delete"][0]
        payload["messages"] = _apply_regex_delete(messages, pattern)
    elif "replace" in logic:
        find, replacement = logic["replace"][0], logic["replace"][1]
        payload["messages"] = _apply_replace(messages, find, replacement)

    return payload


def apply_pipeline(payload: dict, matched_rules: list[dict]) -> tuple[dict, list[dict]]:
    """Apply matched rules in priority order. Returns (final_payload, mutation_steps)."""
    steps = []
    current = copy.deepcopy(payload)

    for rule in matched_rules:
        current = _execute_mutate_logic(current, rule["mutate_logic"])
        steps.append({
            "rule_id": str(rule["id"]),
            "rule_name": rule["name"],
            "priority": rule["priority"],
            "payload_after": copy.deepcopy(current),
        })

    return current, steps
