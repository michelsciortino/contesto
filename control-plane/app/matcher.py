import json
from json_logic import jsonLogic
from app.redis_client import get_redis, RULES_KEY


def build_context(payload: dict) -> dict:
    tools = payload.get("tools") or []
    messages = payload.get("messages") or []
    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
    return {
        "model": payload.get("model", ""),
        "tool_names": [t["name"] for t in tools if "name" in t],
        "message_count": len(messages),
        "has_system": bool(payload.get("system")),
        "total_chars": total_chars,
    }


def evaluate_rules(rules: list[dict], payload: dict) -> list[dict]:
    context = build_context(payload)
    matched = []
    for rule in rules:
        try:
            if jsonLogic(rule["match_logic"], context):
                matched.append(rule)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(
                f"Rule evaluation error for rule {rule.get('id')}: {e}"
            )
    return matched


async def load_rules_from_redis() -> list[dict]:
    redis = get_redis()
    raw = await redis.get(RULES_KEY)
    if not raw:
        return []
    return json.loads(raw)


async def refresh_rules_cache(db_rules: list[dict]) -> None:
    redis = get_redis()
    await redis.set(RULES_KEY, json.dumps(db_rules))
