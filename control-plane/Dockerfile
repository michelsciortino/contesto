FROM python:3.11-slim

WORKDIR /app
COPY control-plane/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy proto-generated stubs so they are baked into the image
COPY protos/generated/interceptor_pb2.py .
COPY protos/generated/interceptor_pb2_grpc.py .

COPY control-plane/app/ ./app/
COPY control-plane/migrations/ ./migrations/
COPY control-plane/alembic.ini .

EXPOSE 50051 8080

CMD ["python", "-m", "app.main"]