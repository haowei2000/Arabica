export async function withRetry(operation, options = {}) {
  return operation();
}
