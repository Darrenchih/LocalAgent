import { describe, expect, it } from "vitest";
import { DEFAULT_LOCALE, resources } from "./index";

function leafKeys(value: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, child]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return typeof child === "object" && child !== null
      ? leafKeys(child as Record<string, unknown>, path)
      : [path];
  });
}

describe("locales", () => {
  it("defaults to Traditional Chinese", () => {
    expect(DEFAULT_LOCALE).toBe("zh-TW");
  });

  it("keeps every supported locale key-complete", () => {
    const expected = leafKeys(resources["zh-TW"].translation).sort();
    expect(Object.keys(resources).sort()).toEqual(["en", "zh-CN", "zh-TW"]);
    for (const locale of Object.values(resources)) {
      expect(leafKeys(locale.translation).sort()).toEqual(expected);
    }
  });
});
