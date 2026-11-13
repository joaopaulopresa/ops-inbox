// Plain-language labels and formatting shared by the screens.

const FIELD_LABELS: Record<string, string> = {
  customer_name: "Customer",
  customer_phone: "Phone",
  car: "Car",
  pickup_date: "Pick-up date",
  pickup_time: "Pick-up time",
  return_date: "Return date",
  return_time: "Return time",
  pickup_location: "Pick-up place",
  purpose: "Purpose",
  extras: "Extra",
  quoted_total: "Price the customer expects",
  holder_name: "Holder",
  licence_number: "Licence number",
  date_of_birth: "Date of birth",
  expires_on: "Valid until",
  classes: "Classes",
  supplier_name: "Supplier",
  invoice_number: "Invoice number",
  invoice_date: "Invoice date",
  subtotal: "Subtotal",
  tax: "Tax",
  total: "Total",
  lines: "Lines",
};

const CATEGORY_LABELS: Record<string, string> = {
  new_booking: "New booking",
  change: "Change",
  cancellation: "Cancellation",
  question: "Question",
  supplier_invoice: "Supplier invoice",
  not_relevant: "Not relevant",
};

const CARS: Record<string, string> = {
  KESTREL: "1961 Hartwell Kestrel",
  SPRITE: "1965 Aldermoor Sprite",
  IMPERIAL: "1958 Castellan Imperial",
  CORSAIR: "1969 Brightwater Corsair",
  REGENT: "1954 Wexcombe Regent",
  MARLOWE: "1972 Marlowe GT",
  SKYLARK: "1963 Tindall Skylark",
  PHANTOM: "1949 Ashdown Eight",
  DART: "1967 Fenwick Dart",
  VELOCE: "1975 Orsini Veloce",
  COUNTESS: "1959 Pemberley Countess",
  LINDQVIST: "1966 Lindqvist 1800 Sport",
};

export const fieldLabel = (name: string) => FIELD_LABELS[name] ?? name.replace(/_/g, " ");
export const categoryLabel = (name: string | null) =>
  name ? (CATEGORY_LABELS[name] ?? name) : "Not sorted yet";
export const carName = (code: string | null) => (code ? (CARS[code] ?? code) : "");
export const carCodes = Object.keys(CARS);

export function displayValue(name: string, value: string | null): string {
  if (value === null || value === "") return "";
  if (name === "car") return carName(value);
  if (/_date$|^expires_on$|^date_of_birth$/.test(name)) {
    const date = new Date(`${value}T00:00:00`);
    if (!Number.isNaN(date.getTime())) {
      return date.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
    }
  }
  if (name === "purpose" || name === "extras") return value.replace(/_/g, " ");
  return value;
}

export function urgency(iso: string | null, now: Date = new Date()): string {
  if (!iso) return "No date";
  const date = new Date(iso);
  const days = Math.round((date.getTime() - now.getTime()) / 86_400_000);
  const day = date.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  if (days < 0) return `${day}, passed`;
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days < 14) return `In ${days} days`;
  return day;
}

export function received(iso: string): string {
  return new Date(iso).toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export const percent = (value: number) => `${Math.round(value * 100)}%`;

/** Short plain-language version of a stop reason, for queue rows. */
export function shortReason(code: string): string {
  if (code.startsWith("missing:")) return `${fieldLabel(code.slice(8))} missing`;
  if (code.startsWith("low_confidence:")) {
    const name = code.slice(15).replace(/^(licence|invoice)\./, "");
    return `Unsure: ${fieldLabel(name).toLowerCase()}`;
  }
  const known: Record<string, string> = {
    car_available: "Car already booked",
    car_known: "Unknown car",
    licence_attached: "No licence",
    licence_readable: "Licence unreadable",
    licence_valid_for_rental: "Licence expires",
    licence_class: "Wrong licence class",
    driver_age: "Driver too young",
    licence_holder_matches: "Licence of someone else",
    price_matches_tariff: "Price differs",
    not_duplicate: "Possible duplicate",
    dates_in_order: "Dates out of order",
    dates_in_future: "Date in the past",
    rental_length: "Long rental",
    booking_found: "Booking not found",
    sender_is_customer: "Not from the customer",
    booking_still_open: "Booking closed",
    change_has_content: "Unclear change",
    invoice_attached: "No invoice",
    invoice_totals_add_up: "Totals do not add up",
    supplier_known: "Unknown supplier address",
    invoice_not_duplicate: "Invoice already recorded",
    suspicious_content: "Tries to steer the process",
    sender_consistent: "Sender mismatch",
    needs_answer: "Question to answer",
    irrelevant_has_no_booking_signals: "Check before archiving",
    records_changed: "Records changed",
    low_confidence: "Uncertain field",
    model_unavailable: "Model unavailable",
    unclassified: "Not sorted",
    category: "Handled by a person",
  };
  return known[code] ?? code.replace(/_/g, " ");
}
