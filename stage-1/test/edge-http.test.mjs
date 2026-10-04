import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { editable } from "../dist/domain.js";
const fixture = {
  users: [
    { id: "u", email: "a@b", password: "password", display_name: "A" },
    { id: "v", email: "v@b", password: "password", display_name: "V" },
  ],
  restaurants: [
    {
      id: "r",
      name: "R",
      timezone: "UTC",
      slot_minutes: 30,
      reservation_duration_minutes: 90,
      cancellation_cutoff_minutes: 120,
      opening_hours: [{ weekday: "thu", opens: "18:00", closes: "23:00" }],
      tables: [
        { id: "t1", label: "1", capacity: 4 },
        { id: "t2", label: "2", capacity: 4 },
      ],
    },
  ],
  reservations: [],
};
fixture.restaurants.push({
  ...structuredClone(fixture.restaurants[0]),
  id: "other",
});
const booking = {
  restaurant_id: "r",
  table_id: "t1",
  starts_at_local: "2030-09-26T19:00",
  party_size: 2,
};
async function service(t, port) {
  const child = spawn(process.execPath, ["dist/server.js"], {
    env: { ...process.env, PORT: String(port) },
    stdio: "pipe",
  });
  t.after(() => child.kill());
  const base = `http://127.0.0.1:${port}`;
  async function call(path, method = "GET", body, token, key) {
    const r = await fetch(base + path, {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: "Bearer " + token } : {}),
        ...(key ? { "Idempotency-Key": key } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(path.startsWith("/_test/") ? 10000 : 5000),
    });
    return { status: r.status, body: r.status === 204 ? null : await r.json() };
  }
  for (let i = 0; i < 100; i++) {
    try {
      if ((await call("/health")).status === 200) break;
    } catch {}
    await delay(25);
  }
  return call;
}
async function setup(t, port) {
  const call = await service(t, port);
  assert.equal((await call("/_test/reset", "POST", fixture)).status, 204);
  const login = async (email) =>
    (await call("/auth/login", "POST", { email, password: "password" })).body
      .token;
  return { call, token: await login("a@b"), other: await login("v@b") };
}
const expectCode = (r, status, code) => {
  assert.equal(r.status, status);
  assert.equal(r.body.error.code, code);
};
test("50 distinct-key races, adjacency, private ownership and failed-key reuse", async (t) => {
  const { call, token, other } = await setup(t, 18243);
  const attempts = await Promise.all(
    Array.from({ length: 50 }, (_, i) =>
      call("/reservations", "POST", booking, token, "race" + i),
    ),
  );
  assert.equal(attempts.filter((r) => r.status === 201).length, 1);
  assert.equal(
    attempts.filter(
      (r) => r.status === 409 && r.body.error.code === "table_unavailable",
    ).length,
    49,
  );
  const winner = attempts.find((r) => r.status === 201).body;
  for (const [method, path, body] of [
    ["GET", "", undefined],
    ["PATCH", "", { party_size: 3 }],
    ["POST", "/cancel", {}],
  ])
    expectCode(
      await call(
        "/reservations/" + winner.reference + path,
        method,
        body,
        other,
      ),
      404,
      "not_found",
    );
  const loser = attempts.findIndex((r) => r.status === 409);
  assert.equal(
    (
      await call(
        "/reservations",
        "POST",
        { ...booking, starts_at_local: "2030-09-26T20:30" },
        token,
        "race" + loser,
      )
    ).status,
    201,
  );
  const available = await call(
    "/availability?restaurant_id=r&date=2030-09-26&party_size=2",
  );
  assert.deepEqual(
    available.body.slots.find((s) => s.starts_at_local.endsWith("T20:30"))
      .available_table_ids,
    ["t2"],
  );
});
test("batch unchanged blockers, mixed owners/restaurants, receipts survive changes and import", async (t) => {
  const { call, token, other } = await setup(t, 18244);
  const dest = await service(t, 18245);
  const a = (await call("/reservations", "POST", booking, token, "a")).body;
  const b = (
    await call(
      "/reservations",
      "POST",
      { ...booking, table_id: "t2" },
      token,
      "b",
    )
  ).body;
  const foreign = (
    await call(
      "/reservations",
      "POST",
      { ...booking, restaurant_id: "other" },
      other,
      "f",
    )
  ).body;
  const elsewhere = (
    await call(
      "/reservations",
      "POST",
      { ...booking, restaurant_id: "other", table_id: "t2" },
      token,
      "e",
    )
  ).body;
  const state = (await call("/_test/export")).body;
  for (const [body, code, status] of [
    [
      {
        moves: [
          { reference: a.reference, table_id: "t2" },
          { reference: b.reference },
        ],
      },
      "table_unavailable",
      409,
    ],
    [
      {
        moves: [
          { reference: a.reference, party_size: 3 },
          { reference: foreign.reference },
        ],
      },
      "not_found",
      404,
    ],
    [
      {
        moves: [
          { reference: a.reference, party_size: 3 },
          { reference: elsewhere.reference },
        ],
      },
      "validation_failed",
      422,
    ],
  ]) {
    expectCode(
      await call("/reservation-moves", "POST", body, token, "failed"),
      status,
      code,
    );
    assert.deepEqual((await call("/_test/export")).body, state);
  }
  const swap = {
    moves: [
      { reference: a.reference, table_id: "t2" },
      { reference: b.reference, table_id: "t1" },
    ],
  };
  const receipt = await call(
    "/reservation-moves",
    "POST",
    swap,
    token,
    "failed",
  );
  assert.equal(receipt.status, 201);
  assert.equal(
    (await call("/reservations/" + a.reference + "/cancel", "POST", {}, token))
      .status,
    200,
  );
  assert.equal(
    (
      await call(
        "/reservations/" + b.reference,
        "PATCH",
        { party_size: 3 },
        token,
      )
    ).status,
    200,
  );
  const snapshot = (await call("/_test/export")).body;
  assert.equal(
    (
      await dest("/_test/reset", "POST", {
        ...fixture,
        users: [
          {
            id: "dest",
            email: "dest@b",
            password: "password",
            display_name: "Dest",
          },
        ],
      })
    ).status,
    204,
  );
  const old = (
    await dest("/auth/login", "POST", { email: "dest@b", password: "password" })
  ).body.token;
  for (let i = 0; i < 2; i++) {
    assert.equal((await dest("/_test/import", "POST", snapshot)).status, 204);
    assert.deepEqual(
      await dest("/reservation-moves", "POST", swap, token, "failed"),
      { status: 200, body: receipt.body },
    );
  }
  expectCode(
    await dest("/reservations", "GET", undefined, old),
    401,
    "unauthenticated",
  );
  expectCode(
    await dest("/auth/login", "POST", {
      email: "dest@b",
      password: "password",
    }),
    401,
    "unauthenticated",
  );
  assert.equal(
    (
      await dest("/_test/reset", "POST", {
        users: [],
        restaurants: [],
        reservations: [],
      })
    ).status,
    204,
  );
  expectCode(
    await dest("/reservations", "GET", undefined, token),
    401,
    "unauthenticated",
  );
});
test("invalid input codes and malformed imported records are atomic", async (t) => {
  const { call, token } = await setup(t, 18246);
  const created = await call("/reservations", "POST", booking, token, "saved");
  assert.equal(created.status, 201);
  for (const [field, value, status] of [
    ["party_size", true, 422],
    ["party_size", "2", 422],
    ["table_id", 1, 400],
    ["starts_at_local", 1, 400],
    ["starts_at_local", "2030-09-26T19:00Z", 422],
  ])
    expectCode(
      await call(
        "/reservations",
        "POST",
        { ...booking, [field]: value },
        token,
        "new",
      ),
      status,
      status === 400 ? "malformed_request" : "validation_failed",
    );
  expectCode(
    await call(
      "/reservations",
      "POST",
      { ...booking, table_id: 1 },
      token,
      "saved",
    ),
    409,
    "idempotency_key_reuse",
  );
  const original = (await call("/_test/export")).body;
  const changes = [
    (x) => (x.track = "wrong"),
    (x) => (x.format_version = 2),
    (x) => (x.state.users[0].credential.N = 2),
    (x) => (x.state.sessions[0].user_id = "absent"),
    (x) => (x.state.reservations[0].start += 1),
    (x) => (x.state.receipts[0].response = {}),
    (x) => (x.state.restaurants[0].tables = [null]),
  ];
  for (const mutate of changes) {
    const bad = structuredClone(original);
    mutate(bad);
    expectCode(
      await call("/_test/import", "POST", bad),
      422,
      "validation_failed",
    );
    assert.deepEqual((await call("/_test/export")).body, original);
  }
  expectCode(
    await call("/_test/reset", "POST", {
      ...fixture,
      restaurants: [{ ...fixture.restaurants[0], tables: [null] }],
    }),
    400,
    "malformed_request",
  );
  assert.deepEqual((await call("/_test/export")).body, original);
});
test("reset during outstanding authentication cannot resurrect old sessions", async (t) => {
  const { call, token } = await setup(t, 18247);
  const logins = Array.from({ length: 50 }, () =>
    call("/auth/login", "POST", { email: "a@b", password: "password" }),
  );
  await delay(5);
  assert.equal(
    (
      await call("/_test/reset", "POST", {
        users: [],
        restaurants: [],
        reservations: [],
      })
    ).status,
    204,
  );
  const responses = await Promise.all(logins);
  for (const r of responses) {
    assert.ok([200, 401].includes(r.status));
    if (r.status === 200)
      expectCode(
        await call("/reservations", "GET", undefined, r.body.token),
        401,
        "unauthenticated",
      );
  }
  expectCode(
    await call("/reservations", "GET", undefined, token),
    401,
    "unauthenticated",
  );
  assert.deepEqual((await call("/_test/export")).body.state.users, []);
  assert.deepEqual((await call("/_test/export")).body.state.sessions, []);
});
test("cutoff is inclusive and measured against existing start", () => {
  const b = {
    status: "confirmed",
    start: 10000000,
    rules: { cancellation_cutoff_minutes: 120 },
  };
  assert.doesNotThrow(() => editable(b, 2799999));
  assert.throws(
    () => editable(b, 2800000),
    (e) => e.code === "cutoff_passed",
  );
  assert.throws(
    () => editable(b, 2800001),
    (e) => e.code === "cutoff_passed",
  );
});
test("HTTP DST gap/fold availability, absolute duration and past cutoff", async (t) => {
  const call = await service(t, 18248);
  for (const [zone, gap, fold, offset, end] of [
    [
      "Europe/Berlin",
      "2026-03-29T02:30",
      "2026-10-25T02:30",
      "+02:00",
      "2026-10-25T03:00:00+01:00",
    ],
    [
      "America/New_York",
      "2026-03-08T02:30",
      "2026-11-01T01:30",
      "-04:00",
      "2026-11-01T02:00:00-05:00",
    ],
  ]) {
    const f = structuredClone(fixture);
    f.restaurants = [
      {
        ...f.restaurants[0],
        timezone: zone,
        opening_hours: [{ weekday: "sun", opens: "00:00", closes: "05:00" }],
      },
    ];
    assert.equal((await call("/_test/reset", "POST", f)).status, 204);
    const token = (
      await call("/auth/login", "POST", { email: "a@b", password: "password" })
    ).body.token;
    const availability = (
      await call(
        "/availability?restaurant_id=r&date=" +
          gap.slice(0, 10) +
          "&party_size=2",
      )
    ).body;
    assert.ok(!availability.slots.some((s) => s.starts_at_local === gap));
    expectCode(
      await call(
        "/reservations",
        "POST",
        { ...booking, starts_at_local: gap },
        token,
        "gap",
      ),
      422,
      "invalid_local_time",
    );
    const foldSlots = (
      await call(
        "/availability?restaurant_id=r&date=" +
          fold.slice(0, 10) +
          "&party_size=2",
      )
    ).body.slots;
    assert.equal(foldSlots.filter((s) => s.starts_at_local === fold).length, 1);
    assert.ok(
      foldSlots
        .find((s) => s.starts_at_local === fold)
        .starts_at.endsWith(offset),
    );
    const made = await call(
      "/reservations",
      "POST",
      { ...booking, starts_at_local: fold },
      token,
      "fold",
    );
    assert.equal(made.status, 201);
    assert.equal(made.body.ends_at, end);
    const past = await call(
      "/reservations",
      "POST",
      { ...booking, starts_at_local: gap.slice(0, 10) + "T00:00" },
      token,
      "past",
    );
    assert.equal(past.status, 201);
    expectCode(
      await call(
        "/reservations/" + past.body.reference + "/cancel",
        "POST",
        {},
        token,
      ),
      409,
      "cutoff_passed",
    );
    expectCode(
      await call(
        "/reservations/" + past.body.reference,
        "PATCH",
        { party_size: 0 },
        token,
      ),
      409,
      "cutoff_passed",
    );
  }
});
test("receipt canonical body, path/user scope and simultaneous signup uniqueness", async (t) => {
  const { call, token, other } = await setup(t, 18249);
  const body = { ...booking, ignored: { b: 2, a: 1 } };
  const first = await call("/reservations", "POST", body, token, "shared");
  const reordered = {
    ignored: { a: 1, b: 2 },
    party_size: 2,
    starts_at_local: booking.starts_at_local,
    table_id: "t1",
    restaurant_id: "r",
  };
  assert.deepEqual(
    await call("/reservations", "POST", reordered, token, "shared"),
    { status: 200, body: first.body },
  );
  const move = { moves: [{ reference: first.body.reference }] };
  assert.equal(
    (await call("/reservation-moves", "POST", move, token, "shared")).status,
    201,
  );
  assert.deepEqual(
    (
      await call(
        "/reservations/" + first.body.reference,
        "GET",
        undefined,
        token,
      )
    ).body,
    first.body,
  );
  assert.equal(
    (
      await call(
        "/reservations",
        "POST",
        { ...booking, table_id: "t2" },
        other,
        "shared",
      )
    ).status,
    201,
  );
  const signups = await Promise.all(
    Array.from({ length: 10 }, () =>
      call("/auth/signup", "POST", {
        email: "new@b",
        password: "password",
        display_name: "New",
      }),
    ),
  );
  assert.equal(signups.filter((r) => r.status === 201).length, 1);
  assert.equal(
    signups.filter(
      (r) => r.status === 409 && r.body.error.code === "email_taken",
    ).length,
    9,
  );
});
