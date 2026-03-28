import grpc
from concurrent import futures
import json
import interceptor_pb2
import interceptor_pb2_grpc


class ContextServicer(interceptor_pb2_grpc.ContextServiceServicer):
    def MutateContext(self, request, context):
        print(f" Ricevuta richiesta di mutazione per Trace ID: {request.trace_id}")

        #  Load del payload originale da JSON a dict
        data = json.loads(request.raw_json_payload)

        if "messages" in data:
            data["messages"] = [
                {
                    "role": "user",
                    "content": "SAY CARBONARA!",
                }
            ]

        # Send back the modified payload as a JSON string in the gRPC response
        return interceptor_pb2.ContextResponse(
            action=interceptor_pb2.ContextResponse.Action.MUTATED, modified_json_payload=json.dumps(data)
        )


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    interceptor_pb2_grpc.add_ContextServiceServicer_to_server(ContextServicer(), server)
    server.add_insecure_port("[::]:50051")
    print("Control Plane gRPC server is starting on port 50051...")
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
