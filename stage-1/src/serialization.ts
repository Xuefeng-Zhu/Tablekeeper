import { Temporal } from "@js-temporal/polyfill";
import { empty, type State, type Booking } from "./model.js";
import {
  object,
  id,
  str,
  positive,
  fail,
  clone,
  canonical,
} from "./validation.js";
import { credential, account } from "./auth.js";
import { selection, occupancy, view } from "./domain.js";
import { minute, stamp } from "./time.js";
function unique(xs: any[]) {
  if (new Set(xs).size !== xs.length) fail();
}
function array(x: any): any[] {
  if (x === undefined) fail();
  if (!Array.isArray(x)) fail("malformed_request", 400);
  return x;
}
function count(value: unknown): number {
  if (value !== undefined && typeof value !== "number")
    fail("malformed_request", 400);
  return positive(value);
}
function restaurants(value: any) {
  return array(value).map((v) => {
    object(v);
    const r = {
      id: id(v.id),
      name: str(v.name),
      timezone: str(v.timezone),
      slot_minutes: count(v.slot_minutes),
      reservation_duration_minutes: count(v.reservation_duration_minutes),
      cancellation_cutoff_minutes: v.cancellation_cutoff_minutes,
      opening_hours: array(v.opening_hours).map((h) => ({
        weekday: str(object(h).weekday),
        opens: str(h.opens),
        closes: str(h.closes),
      })),
      tables: array(v.tables).map((t) => ({
        id: id(object(t).id),
        label: str(t.label),
        capacity: count(t.capacity),
      })),
    };
    try {
      Temporal.Now.zonedDateTimeISO(r.timezone);
    } catch {
      fail();
    }
    if (
      r.cancellation_cutoff_minutes !== undefined &&
      typeof r.cancellation_cutoff_minutes !== "number"
    )
      fail("malformed_request", 400);
    if (
      !Number.isSafeInteger(r.cancellation_cutoff_minutes) ||
      r.cancellation_cutoff_minutes < 0
    )
      fail();
    for (const h of r.opening_hours)
      if (
        !["mon", "tue", "wed", "thu", "fri", "sat", "sun"].includes(
          h.weekday,
        ) ||
        minute(h.opens) >= minute(h.closes)
      )
        fail();
    unique(r.tables.map((t) => t.id));
    unique(r.opening_hours.map((h) => h.weekday));
    return r;
  });
}
export async function fixture(body: any): Promise<State> {
  const s = empty();
  s.restaurants = restaurants(body.restaurants);
  unique(s.restaurants.map((r) => r.id));
  s.users = await Promise.all(
    array(body.users).map(async (u) => {
      object(u);
      const a = account(u, true);
      return {
        id: id(u.id),
        email: a.email,
        display_name: a.display_name!,
        credential: await credential(a.password),
      };
    }),
  );
  unique(s.users.map((u) => u.id));
  unique(s.users.map((u) => u.email));
  s.reservations = array(body.reservations).map((b) => {
    object(b);
    if (!s.users.some((u) => u.id === b.user_id)) fail();
    const r: Booking = {
      ...selection(s, b),
      id: id(b.id),
      reference: str(b.reference),
      user_id: id(b.user_id),
      status: "confirmed",
      created_at: stamp(Date.now()),
    };
    if (!/^[A-Z0-9]{6,12}$/.test(r.reference)) fail();
    return r;
  });
  unique(s.reservations.map((b) => b.id));
  unique(s.reservations.map((b) => b.reference));
  occupancy(s, s.reservations);
  return s;
}
export function imported(envelope: any): State {
  try {
    if (envelope.track !== "tablekeeper" || envelope.format_version !== 1)
      fail();
    const s = clone(object(envelope.state)) as State;
    if (s.schema_version !== 1) fail();
    restaurants(s.restaurants);
    unique(s.restaurants.map((r) => r.id));
    array(s.users);
    array(s.sessions);
    array(s.reservations);
    array(s.receipts);
    unique(s.users.map((u) => id(u.id)));
    unique(s.users.map((u) => str(u.email)));
    for (const u of s.users) {
      str(u.display_name);
      const c = u.credential;
      if (
        c.algorithm !== "scrypt" ||
        c.N !== 16384 ||
        c.r !== 8 ||
        c.p !== 1 ||
        !/^[a-f0-9]{32}$/.test(c.salt) ||
        !/^[a-f0-9]{64}$/.test(c.hash)
      )
        fail();
    }
    unique(s.sessions.map((t) => str(t.token)));
    for (const t of s.sessions)
      if (!t.token || !s.users.some((u) => u.id === t.user_id)) fail();
    unique(s.reservations.map((b) => id(b.id)));
    unique(s.reservations.map((b) => str(b.reference)));
    for (const b of s.reservations) {
      if (
        !/^[A-Z0-9]{6,12}$/.test(b.reference) ||
        !s.users.some((u) => u.id === b.user_id) ||
        !["confirmed", "cancelled"].includes(b.status) ||
        !Array.isArray(b.table_ids) ||
        b.table_ids.length !== 1
      )
        fail();
      const normalized = selection(s, { ...b, table_id: b.table_ids[0] });
      if (
        normalized.start !== b.start ||
        normalized.end !== b.end ||
        canonical(normalized.rules) !== canonical(b.rules)
      )
        fail();
      Temporal.Instant.from(b.created_at);
    }
    occupancy(s, s.reservations);
    unique(
      s.receipts.map((r) => canonical([r.user_id, r.method, r.path, r.key])),
    );
    for (const r of s.receipts) {
      if (
        !s.users.some((u) => u.id === r.user_id) ||
        r.method !== "POST" ||
        !["/reservations", "/reservation-moves"].includes(r.path) ||
        typeof r.key !== "string" ||
        r.key.length < 1 ||
        r.key.length > 255 ||
        canonical(object(r.body)) !== r.canonical
      )
        fail();
      const responses =
        r.path === "/reservations"
          ? [object(r.response)]
          : array(object(r.response).reservations);
      if (responses.length < 1 || responses.length > 8) fail();
      if (r.path === "/reservation-moves") {
        const inputs = array(r.body.moves);
        if (inputs.length !== responses.length) fail();
        unique(inputs.map((m) => str(object(m).reference)));
        for (let i = 0; i < inputs.length; i++) {
          if (inputs[i].reference !== responses[i].reference) fail();
          for (const field of ["table_id", "starts_at_local", "party_size"])
            if (
              inputs[i][field] !== undefined &&
              inputs[i][field] !== responses[i][field]
            )
              fail();
        }
      }
      for (const response of responses) {
        const current = s.reservations.find(
          (b) =>
            b.id === response.reservation_id &&
            b.reference === response.reference &&
            b.user_id === r.user_id,
        );
        if (
          !current ||
          response.status !== "confirmed" ||
          response.created_at !== current.created_at ||
          response.restaurant_id !== current.restaurant_id
        )
          fail();
        const selected = selection(s, response);
        const historical = {
          ...current,
          ...selected,
          status: "confirmed" as const,
        };
        if (canonical(view(historical)) !== canonical(response)) fail();
      }
      if (r.path === "/reservations") {
        const expected = selection(s, r.body);
        const actual = selection(s, r.response);
        if (canonical(expected) !== canonical(actual)) fail();
      }
    }
    return s;
  } catch {
    fail();
  }
}
