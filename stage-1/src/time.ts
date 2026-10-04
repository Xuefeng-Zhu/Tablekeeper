import { Temporal } from "@js-temporal/polyfill";
import { fail, str } from "./validation.js";
export function date(x: any) {
  const s = str(x);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) fail();
  try {
    return Temporal.PlainDate.from(s);
  } catch {
    fail();
  }
}
export function local(x: any) {
  const s = str(x);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(s)) fail();
  try {
    return Temporal.PlainDateTime.from(s);
  } catch {
    fail();
  }
}
export function instant(s: string, zone: string) {
  const p = local(s);
  const z = p.toZonedDateTime(zone, { disambiguation: "earlier" });
  if (!z.toPlainDateTime().equals(p)) fail("invalid_local_time");
  return z.epochMilliseconds;
}
export function stamp(ms: number, zone = "UTC") {
  return Temporal.Instant.fromEpochMilliseconds(ms)
    .toZonedDateTimeISO(zone)
    .toString({
      smallestUnit: "second",
      timeZoneName: "never",
      calendarName: "never",
    });
}
export function minute(s: string) {
  if (typeof s !== "string" || !/^([01]\d|2[0-3]):[0-5]\d$/.test(s)) fail();
  return +s.slice(0, 2) * 60 + +s.slice(3);
}
export function at(day: string, m: number) {
  return (
    day +
    "T" +
    String(Math.floor(m / 60)).padStart(2, "0") +
    ":" +
    String(m % 60).padStart(2, "0")
  );
}
export function boundary(day: string, m: number, zone: string) {
  for (; m < 1440; m++) {
    try {
      return instant(at(day, m), zone);
    } catch (e) {
      if ((e as any).code !== "invalid_local_time") throw e;
    }
  }
  fail("outside_opening_hours");
}
export function hours(r: any, day: string) {
  return r.opening_hours.find(
    (h: any) =>
      h.weekday ===
      ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][
        date(day).dayOfWeek - 1
      ],
  );
}
