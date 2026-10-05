import { describe, expect, it } from "vitest";
import {
  PASSWORD_MIN_LENGTH,
  validateEmail,
  validatePassword,
} from "@/lib/authClient";

describe("password policy", () => {
  it("mirrors the backend minimum length", () => {
    // If these drift apart, the form accepts something the API refuses and the
    // failure arrives as a round trip instead of a message under the field.
    expect(PASSWORD_MIN_LENGTH).toBe(12);
    expect(validatePassword("a".repeat(PASSWORD_MIN_LENGTH))).toBeNull();
    expect(validatePassword("a".repeat(PASSWORD_MIN_LENGTH - 1))).not.toBeNull();
  });

  it("catches a mismatched confirmation before the request", () => {
    expect(validatePassword("correct horse battery", "correct horse batter")).toMatch(/match/i);
    expect(validatePassword("correct horse battery", "correct horse battery")).toBeNull();
  });

  it("skips the comparison when no confirmation is supplied", () => {
    // The invitation flow treats an empty password as "keep the one I have",
    // so undefined must not be compared against a real value.
    expect(validatePassword("correct horse battery", undefined)).toBeNull();
  });
});

describe("email validation", () => {
  it("rejects what the backend would reject", () => {
    expect(validateEmail("ops@example.com")).toBeNull();
    expect(validateEmail("  ops@example.com  ")).toBeNull();
    expect(validateEmail("")).not.toBeNull();
    expect(validateEmail("   ")).not.toBeNull();
    expect(validateEmail("@example.com")).not.toBeNull();
    expect(validateEmail("ops.example.com")).not.toBeNull();
  });

  it("does not try to out-validate the server", () => {
    // Deliberately shallow. A stricter client regex starts rejecting addresses
    // the server accepts, and the person is told their own address is invalid.
    expect(validateEmail("ops+tag@example.co.uk")).toBeNull();
    expect(validateEmail("ops@localhost")).toBeNull();
  });
});
