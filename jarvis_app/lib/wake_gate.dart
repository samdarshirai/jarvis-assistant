/// Wake word listens only when enabled and on power or in the car.
bool wakeGateOpen({
  required bool enabled,
  required bool charging,
  required bool androidAuto,
}) =>
    enabled && (charging || androidAuto);
