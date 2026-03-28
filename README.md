



## Generate protobuf code

```shell
mkdir -p protos/generated
python -m grpc_tools.protoc -I./protos \
       --python_out=./protos/generated \
       --grpc_python_out=./protos/generated ./protos/interceptor.proto
```