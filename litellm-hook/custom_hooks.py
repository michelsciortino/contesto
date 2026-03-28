import json

import grpc
import interceptor_pb2
import interceptor_pb2_grpc
from litellm.integrations.custom_logger import CustomLogger

CONTROL_PLANE_TARGET = "control-plane:50051"
GRPC_TIMEOUT = 2.0

# Keys safe to overwrite from the control plane response.
# LiteLLM attaches internal objects to `data` that must not be replaced.
MUTABLE_KEYS = frozenset(
    {
        "messages",
        "model",
        "temperature",
        "max_tokens",
        "top_p",
        "stream",
        "stop",
        "tools",
        "tool_choice",
    }
)

Action = interceptor_pb2.ContextResponse.Action


class GovernorHook(CustomLogger):
    """LiteLLM callback that forwards every request to the Governor control plane."""

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        try:
            async with grpc.aio.insecure_channel(CONTROL_PLANE_TARGET) as channel:
                stub = interceptor_pb2_grpc.ContextServiceStub(channel)
                request = interceptor_pb2.ContextRequest(
                    trace_id=str(data.get("litellm_call_id", "")),
                    model=data.get("model", ""),
                    raw_json_payload=json.dumps(data, default=str),
                    metadata={"source": "litellm-hook"},
                )
                response = await stub.MutateContext(request, timeout=GRPC_TIMEOUT)

            if response.action == Action.MUTATED:
                modified = json.loads(response.modified_json_payload)
                for key in MUTABLE_KEYS & modified.keys():
                    data[key] = modified[key]

            elif response.action == Action.REJECT:
                raise Exception("Request blocked by the Governor Control Plane")
        except Exception as e:
            # Fail-open: let the original request through
            print(f"Governor Error: {e}")
        return data


proxy_handler_instance = GovernorHook()
