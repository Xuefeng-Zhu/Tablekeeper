export class Fault extends Error {
  constructor(
    public status: number,
    public code: string,
  ) {
    super(code.replaceAll("_", " "));
  }
}
export function fail(code = "validation_failed", status = 422): never {
  throw new Fault(status, code);
}
export function object(x: unknown): Record<string, any> {
  if (!x || typeof x !== "object" || Array.isArray(x))
    fail("malformed_request", 400);
  return x as Record<string, any>;
}
export function str(x: any): string {
  if (x === undefined) fail();
  if (typeof x !== "string") fail("malformed_request", 400);
  return x;
}
export function id(x: any): string {
  const s = str(x);
  if (!s.length || s.length > 64) fail();
  return s;
}
export function positive(x: any): number {
  if (typeof x !== "number" || !Number.isSafeInteger(x) || x < 1) fail();
  return x;
}
export function canonical(x: any): string {
  if (Array.isArray(x)) return "[" + x.map(canonical).join(",") + "]";
  if (x && typeof x === "object")
    return (
      "{" +
      Object.keys(x)
        .sort()
        .map((k) => JSON.stringify(k) + ":" + canonical(x[k]))
        .join(",") +
      "}"
    );
  return JSON.stringify(x);
}
export const clone = <T>(x: T): T => structuredClone(x);
