import assert from "node:assert/strict";
import test from "node:test";
import { getTimes } from "suncalc";
import { brokerLoginMedia, brokerLoginPeriod } from "../src/lib/brokerLoginMedia.ts";

test("the Marina Bay scene follows Singapore time for all five periods", () => {
  for (const [time, period] of [
    ["2026-10-12T05:00:00+08:00", "night"],
    ["2026-10-12T07:00:00+08:00", "sunrise"],
    ["2026-10-12T09:00:00+08:00", "morning"],
    ["2026-10-12T15:00:00+08:00", "afternoon"],
    ["2026-10-12T18:50:00+08:00", "sunset"],
    ["2026-10-12T22:00:00+08:00", "night"],
  ] as const) assert.equal(brokerLoginPeriod(new Date(time)), period, time);
});

test("the dawn and dusk boundaries follow the date's actual solar events", () => {
  for (const date of ["2026-01-15", "2026-07-15", "2026-10-12"]) {
    const times = getTimes(new Date(`${date}T12:00:00+08:00`), 1.2833, 103.8583);
    const sunrise = times.sunrise!.getTime(), sunset = times.sunset!.getTime();
    const halfHour = 30 * 60_000;
    assert.equal(brokerLoginPeriod(new Date(sunrise - halfHour - 1)), "night");
    assert.equal(brokerLoginPeriod(new Date(sunrise - halfHour)), "sunrise");
    assert.equal(brokerLoginPeriod(new Date(sunrise + halfHour)), "morning");
    assert.equal(brokerLoginPeriod(new Date(sunset - halfHour - 1)), "afternoon");
    assert.equal(brokerLoginPeriod(new Date(sunset - halfHour)), "sunset");
    assert.equal(brokerLoginPeriod(new Date(sunset + halfHour)), "night");
  }
});

test("noon and Singapore midnight do not depend on the browser's timezone", () => {
  assert.equal(brokerLoginPeriod(new Date("2026-10-12T03:59:59.999Z")), "morning");
  assert.equal(brokerLoginPeriod(new Date("2026-10-12T04:00:00Z")), "afternoon");
  assert.equal(brokerLoginPeriod(new Date("2026-12-31T16:00:00Z")), "night");
  assert.equal(brokerLoginPeriod(new Date("2027-01-01T07:15:00+08:00")), "sunrise");
  assert.equal(brokerLoginPeriod(new Date("2027-01-01T00:00:00-08:00")), "afternoon");
});

test("invalid clocks retain a usable default scene", () => {
  assert.equal(brokerLoginPeriod(new Date(NaN)), "afternoon");
});

test("each period selects a versioned video and its matching poster", () => {
  for (const period of ["sunrise", "morning", "afternoon", "sunset", "night"] as const) {
    const media = brokerLoginMedia(period);
    assert.equal(media.poster, media.video.replace(/\.mp4$/, ".webp"));
    assert.match(media.video, new RegExp(`/marina-bay-${period}-v3\\.mp4$`));
  }
});
