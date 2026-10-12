import { getTimes } from "suncalc";

export type BrokerLoginPeriod = "sunrise" | "morning" | "afternoon" | "sunset" | "night";

// The scene is Marina Bay. Its light follows Singapore, wherever the viewer is.
const SCENE_LATITUDE = 1.2833;
const SCENE_LONGITUDE = 103.8583;
const HALF_HOUR = 30 * 60_000;
const singaporeDate = new Intl.DateTimeFormat("en-SG", {
  timeZone: "Asia/Singapore", year: "numeric", month: "numeric", day: "numeric",
});
let daylight: { day: string; sunrise: number; noon: number; sunset: number } | undefined;

/** Calculate locally once per Singapore date; no location permission or API. */
export function brokerLoginPeriod(date = new Date()): BrokerLoginPeriod {
  const now = date.getTime();
  if (!Number.isFinite(now)) return "afternoon";
  const parts = singaporeDate.formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find(p => p.type === type)?.value);
  const year = part("year"), month = part("month"), day = part("day");
  const dayKey = `${year}-${month}-${day}`;
  if (daylight?.day !== dayKey) {
    // Use Singapore noon (04:00 UTC) so SunCalc chooses this calendar day's
    // solar events even when the viewer opens the page just after midnight.
    const noon = Date.UTC(year, month - 1, day, 4);
    const times = getTimes(new Date(noon), SCENE_LATITUDE, SCENE_LONGITUDE);
    daylight = {
      day: dayKey,
      sunrise: times.sunrise?.getTime() ?? noon - 5 * 60 * 60_000,
      noon,
      sunset: times.sunset?.getTime() ?? noon + 7 * 60 * 60_000,
    };
  }
  if (now < daylight.sunrise - HALF_HOUR) return "night";
  if (now < daylight.sunrise + HALF_HOUR) return "sunrise";
  if (now < daylight.noon) return "morning";
  if (now < daylight.sunset - HALF_HOUR) return "afternoon";
  if (now < daylight.sunset + HALF_HOUR) return "sunset";
  return "night";
}

export function brokerLoginMedia(period: BrokerLoginPeriod) {
  const path = `/broker/login/marina-bay-${period}-v3`;
  return { video: `${path}.mp4`, poster: `${path}.webp` };
}
