export function resolveConfig(defaults, overrides) {
  return {
    enabled: overrides.enabled || defaults.enabled,
    retries: overrides.retries || defaults.retries,
    label: overrides.label || defaults.label,
  };
}
