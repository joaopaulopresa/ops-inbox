import { describe, expect, it } from "vitest";
import { displayValue, shortReason, urgency } from "./format";

describe("format", () => {
  it("names cars and dates the way staff say them", () => {
    expect(displayValue("car", "SPRITE")).toBe("1965 Aldermoor Sprite");
    expect(displayValue("pickup_date", "2027-06-12")).toBe("Sat, 12 Jun 2027");
    expect(displayValue("extras", "wedding_decoration")).toBe("wedding decoration");
  });

  it("turns stop reasons into short plain words", () => {
    expect(shortReason("missing:return_time")).toBe("Return time missing");
    expect(shortReason("low_confidence:licence.expires_on")).toBe("Unsure: valid until");
    expect(shortReason("car_available")).toBe("Car already booked");
  });

  it("states urgency relative to now", () => {
    const now = new Date("2027-06-10T09:00:00");
    expect(urgency("2027-06-10T15:00:00", now)).toBe("Today");
    expect(urgency("2027-06-11T09:00:00", now)).toBe("Tomorrow");
    expect(urgency("2027-06-14T09:00:00", now)).toBe("In 4 days");
    expect(urgency(null, now)).toBe("No date");
  });
});
