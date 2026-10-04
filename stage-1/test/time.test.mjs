import test from "node:test";
import assert from "node:assert/strict";
import { instant, stamp } from "../dist/time.js";
test("both required DST gaps, first folds, absolute durations", () => {
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
    assert.throws(
      () => instant(gap, zone),
      (e) => e.code === "invalid_local_time",
    );
    const start = instant(fold, zone);
    assert.ok(stamp(start, zone).endsWith(offset));
    assert.equal(stamp(start + 90 * 60000, zone), end);
  }
});
