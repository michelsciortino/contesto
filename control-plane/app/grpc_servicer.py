import json
import uuid
import logging
import sys
import os

# Allow importing generated proto files from the app directory at runtime
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import interceptor_pb2
import interceptor_pb2_grpc
from app.matcher import load_rules_from_redis, evaluate_rules
from app.mutator import apply_pipeline
from app.recorder import should_record, push_trace

logger = logging.getLogger(__name__)


class GovernorServicer(interceptor_pb2_grpc.ContextServiceServicer):

    async def MutateContext(self, request, context):
        try:
            payload = json.loads(request.raw_json_payload)
            rules = await load_rules_from_redis()
            matched = evaluate_rules(rules, payload)

            if not matched:
                return interceptor_pb2.ContextResponse(
                    action=interceptor_pb2.ContextResponse.Action.PROCEED,
                    modified_json_payload="",
                )

            final_payload, steps = apply_pipeline(payload, matched)

            session_id = await should_record()
            if session_id:
                await push_trace(session_id, {
                    "trace_id": request.trace_id or str(uuid.uuid4()),
                    "model": request.model,
                    "original_payload": payload,
                    "final_payload": final_payload,
                    "mutation_steps": steps,
                    "action": "MUTATED",
                })

            return interceptor_pb2.ContextResponse(
                action=interceptor_pb2.ContextResponse.Action.MUTATED,
                modified_json_payload=json.dumps(final_payload, default=str),
            )

        except Exception as e:
            logger.error(
                f"Governor servicer error for trace {request.trace_id}: {e}",
                exc_info=True,
            )
            # Fail-open: return PROCEED so LiteLLM lets the original request through
            return interceptor_pb2.ContextResponse(
                action=interceptor_pb2.ContextResponse.Action.PROCEED,
                modified_json_payload="",
            )
