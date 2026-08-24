export function resolveConfig(defaults, overrides = {}) {
  return { ...defaults, ...overrides };
}
