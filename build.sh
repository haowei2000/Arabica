REGISTRY="${REGISTRY:-ghcr.io/your-org}"
BASE="${BASE:-${REGISTRY}/structure/backend}"
DATE_TAG="$(date +%F)"

docker buildx build \
  --platform linux/amd64 \
  --no-cache \
  -f docker/Dockerfile \
  -t "${BASE}:${DATE_TAG}" \
  -t "${BASE}:latest" \
  --load \
  .
