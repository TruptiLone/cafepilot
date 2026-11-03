import { useEffect, useState } from "react";
import { listEvents } from "../api/agent";

/** "Thu Nov 5, 6 PM" in café time (the demo clock is pinned to when the demo events were detected). */
export function demoDateLabel(iso: string): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/Los_Angeles",
  }).formatToParts(new Date(iso));
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  const minute = part("minute") === "00" ? "" : `:${part("minute")}`;
  return `${part("weekday")} ${part("month")} ${part("day")}, ${part("hour")}${minute} ${part("dayPeriod")}`;
}

/** The fixed demo clock, from the demo events; null when there is none (a real café runs on real time). */
export function useDemoDate(): string | null {
  const [label, setLabel] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    listEvents(controller.signal)
      .then((events) => {
        const asOf = events.find((e) => e.is_demo && e.knowledge_as_of)?.knowledge_as_of;
        setLabel(asOf ? demoDateLabel(asOf) : null);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);
  return label;
}
