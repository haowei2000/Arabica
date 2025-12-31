REGISTRY="10.1.2.111:9443"
BASE="${REGISTRY}/ai/python-fastapi"
DATE_TAG="$(date +%F)"

docker buildx build \
  --platform linux/amd64 \
  --no-cache \
  -f docker/Dockerfile \
  -t "${BASE}:${DATE_TAG}" \
  -t "${BASE}:latest" \
  --load \
  .
