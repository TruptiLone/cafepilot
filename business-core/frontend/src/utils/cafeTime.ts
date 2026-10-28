export const CAFE_TIME_ZONE = "America/Los_Angeles";

export type CafeTimestampOption = {
  timestamp: string;
  label: string;
};

export type CafeTimestampResolution =
  | { status: "valid"; timestamp: string }
  | { status: "ambiguous"; options: CafeTimestampOption[] }
  | { status: "nonexistent" }
  | { status: "invalid" };

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

const optionFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: CAFE_TIME_ZONE,
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
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

function formatOffset(offsetMilliseconds: number) {
  const totalMinutes = Math.round(offsetMilliseconds / 60_000);
  const sign = totalMinutes >= 0 ? "+" : "-";
  const absoluteMinutes = Math.abs(totalMinutes);
  const hours = Math.floor(absoluteMinutes / 60).toString().padStart(2, "0");
  const minutes = (absoluteMinutes % 60).toString().padStart(2, "0");
  return `UTC${sign}${hours}:${minutes}`;
}

export function resolveCafeTimestamp(localValue: string): CafeTimestampResolution {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(localValue);
  if (!match) return { status: "invalid" };

  const [, year, month, day, hour, minute] = match.map(Number);
  const normalized = new Date(Date.UTC(year, month - 1, day, hour, minute));
  if (
    normalized.getUTCFullYear() !== year ||
    normalized.getUTCMonth() !== month - 1 ||
    normalized.getUTCDate() !== day ||
    normalized.getUTCHours() !== hour ||
    normalized.getUTCMinutes() !== minute
  ) {
    return { status: "invalid" };
  }

  const wallTime = Date.UTC(year, month - 1, day, hour, minute);
  const offsets = new Set<number>();
  for (let hoursFromWallTime = -48; hoursFromWallTime <= 48; hoursFromWallTime += 6) {
    offsets.add(
      getTimeZoneOffset(new Date(wallTime + hoursFromWallTime * 60 * 60 * 1000)),
    );
  }

  const candidates = [...offsets]
    .map((offset) => ({ candidate: new Date(wallTime - offset), offset }))
    .filter(({ candidate }) => {
      const parts = getTimeZoneParts(candidate);
      return (
        parts.year === year &&
        parts.month === month &&
        parts.day === day &&
        parts.hour === hour &&
        parts.minute === minute
      );
    })
    .sort((left, right) => left.candidate.getTime() - right.candidate.getTime());

  if (!candidates.length) return { status: "nonexistent" };
  if (candidates.length === 1) {
    return { status: "valid", timestamp: candidates[0].candidate.toISOString() };
  }
  return {
    status: "ambiguous",
    options: candidates.map(({ candidate, offset }) => ({
      timestamp: candidate.toISOString(),
      label: `${optionFormatter.format(candidate)} (${formatOffset(offset)})`,
    })),
  };
}

export function toCafeTimestamp(localValue: string) {
  const resolution = resolveCafeTimestamp(localValue);
  return resolution.status === "valid" ? resolution.timestamp : null;
}
