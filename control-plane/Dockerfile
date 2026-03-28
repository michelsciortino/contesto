FROM python:3.13-slim

WORKDIR /app
COPY ./control-plane/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./protos/generated/* .
COPY ./control-plane/*.py .

# Expose the gRPC server port
EXPOSE 50051

CMD ["python", "server.py"]