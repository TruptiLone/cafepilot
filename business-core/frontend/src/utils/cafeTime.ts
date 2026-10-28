export const CAFE_TIME_ZONE = "America/Los_Angeles";

const timeZoneFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: CAFE_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function getTimeZoneParts(date: Date) {
  return Object.fromEntries(
    timeZoneFormatter
      .formatToParts(date)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, Number(part.value)]),
  ) as Record<string, number>;
}

function getTimeZoneOffset(date: Date) {
  const parts = getTimeZoneParts(date);
  const representedAsUtc = Date.UTC(
    parts.year,
    parts.month - 1,
    parts.day,
    parts.hour,
    parts.minute,
    parts.second,
  );
  return representedAsUtc - date.getTime();
}

export function toCafeTimestamp(localValue: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(localValue);
  if (!match) return null;

  const [, year, month, day, hour, minute] = match.map(Number);
  const wallTime = Date.UTC(year, month - 1, day, hour, minute);
  let instant = wallTime;

  for (let attempt = 0; attempt < 3; attempt += 1) {
    instant = wallTime - getTimeZoneOffset(new Date(instant));
  }

  const candidate = new Date(instant);
  const candidateParts = getTimeZoneParts(candidate);
  const isExactMatch =
    candidateParts.year === year &&
    candidateParts.month === month &&
    candidateParts.day === day &&
    candidateParts.hour === hour &&
    candidateParts.minute === minute;

  return isExactMatch ? candidate.toISOString() : null;
}
