import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";

test("import rejects consumed values of the wrong JSON type without changing usable state", async (t) => {
  const child = spawn(process.execPath, ["dist/server.js"], {
    env: { ...process.env, PORT: "18250" },
    stdio: ["ignore", "ignore", "pipe"],
  });
  t.after(() => child.kill());
  let stderr = "";
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
  });
  async function call(path, method = "GET", body, token, key) {
    const response = await fetch("http://127.0.0.1:18250" + path, {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: "Bearer " + token } : {}),
        ...(key ? { "Idempotency-Key": key } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(path.startsWith("/_test/") ? 10000 : 5000),
    });
    return {
      status: response.status,
      body: response.status === 204 ? null : await response.json(),
    };
  }
  for (let n = 0; n < 100; n++) {
    try {
      if ((await call("/health")).status === 200) break;
    } catch {}
    await delay(25);
  }
  const account = {
    email: "types@example.test",
    password: "synthetic-password",
  };
  const fixture = {
    users: [{ id: "u", ...account, display_name: "Types" }],
    restaurants: [
      {
        id: "r",
        name: "R",
        timezone: "UTC",
        slot_minutes: 30,
        reservation_duration_minutes: 90,
        cancellation_cutoff_minutes: 0,
        opening_hours: [{ weekday: "thu", opens: "18:00", closes: "23:00" }],
        tables: [{ id: "t", label: "T", capacity: 4 }],
      },
    ],
    reservations: [],
  };
  assert.equal((await call("/_test/reset", "POST", fixture)).status, 204);
  const token = (await call("/auth/login", "POST", account)).body.token;
  const input = {
    restaurant_id: "r",
    table_id: "t",
    starts_at_local: "2030-09-26T19:00",
    party_size: 2,
  };
  const created = await call("/reservations", "POST", input, token, "original");
  assert.equal(created.status, 201);
  assert.equal(
    (
      await call(
        "/reservations/" + created.body.reference,
        "PATCH",
        { party_size: 3 },
        token,
      )
    ).status,
    200,
  );
  const good = (await call("/_test/export")).body;
  assert.equal((await call("/_test/import", "POST", good)).status, 204);
  assert.deepEqual((await call("/_test/export")).body, good);
  // This historical response deliberately differs from the current booking.
  assert.deepEqual(
    await call("/reservations", "POST", input, token, "original"),
    { status: 200, body: created.body },
  );

  const corruptions = [];
  for (const value of [null, [], "scrypt", 1, true]) {
    corruptions.push([
      "credential container " + JSON.stringify(value),
      (state) => {
        state.users[0].credential = value;
      },
    ]);
  }
  for (const field of ["algorithm", "N", "r", "p", "salt", "hash"]) {
    for (const kind of [
      "array",
      "object",
      "null",
      "boolean",
      "missing",
      "opposite scalar",
    ]) {
      corruptions.push([
        field + " " + kind,
        (state) => {
          const c = state.users[0].credential;
          const current = c[field];
          if (kind === "missing") delete c[field];
          else
            c[field] =
              kind === "array"
                ? [current]
                : kind === "object"
                  ? { value: current }
                  : kind === "null"
                    ? null
                    : kind === "boolean"
                      ? true
                      : typeof current === "string"
                        ? 1
                        : String(current);
        },
      ]);
    }
  }
  for (const field of ["created_at", "reference"]) {
    corruptions.push([
      "booking " + field + " array",
      (state) => {
        state.reservations[0][field] = [state.reservations[0][field]];
      },
    ]);
  }
  for (const [name, corrupt] of corruptions) {
    await t.test(name, async () => {
      assert.equal((await call("/_test/import", "POST", good)).status, 204);
      const bad = structuredClone(good);
      corrupt(bad.state);
      const result = await call("/_test/import", "POST", bad);
      assert.equal(result.status, 422);
      assert.equal(result.body.error.code, "validation_failed");
      assert.deepEqual((await call("/_test/export")).body, good);
      assert.equal((await call("/auth/login", "POST", account)).status, 200);
      assert.equal(
        (await call("/reservations", "GET", undefined, token)).status,
        200,
      );
      assert.deepEqual(
        await call("/reservations", "POST", input, token, "original"),
        { status: 200, body: created.body },
      );
    });
  }
  assert.equal(stderr.includes("Unexpected request failure"), false);
});
