import { AxeBuilder } from "@axe-core/webdriverio";
import { $, browser, expect } from "@wdio/globals";

describe("OpenWhisper Capture in Linux WebKitGTK", () => {
  it("completes the keyboard-accessible capture path without axe violations", async () => {
    const heading = await $("h1=Capture");
    await heading.waitForDisplayed();

    // The embedded WebKit driver cannot create the temporary aggregation
    // window used by axe's cross-frame mode. This app has no frames, so the
    // documented legacy injection path preserves equivalent coverage here.
    const initialA11y = await new AxeBuilder({ client: browser })
      .setLegacyMode()
      .withTags(["wcag2a", "wcag2aa"])
      .analyze();
    expect(initialA11y.violations).toEqual([]);

    const record = await $("button=Record");
    await record.click();
    await expect($("button=Stop")).toBeDisplayed();
    const transcript = await $("[aria-label='Live transcript']");
    await expect(transcript).toHaveText(expect.stringContaining("مرحبا OpenWhisper"));
    await expect(transcript).toHaveAttribute("dir", "auto");
    await expect($("[role='meter']")).toHaveAttribute(
      "aria-valuetext",
      expect.stringContaining("peak"),
    );

    await $("button=Stop").click();
    await expect($("dd=Inserted")).toBeDisplayed();

    await browser.keys(["Control", "k"]);
    await expect($("[role='dialog']")).toBeDisplayed();
    await expect($("kbd=alt + o")).toBeDisplayed();
    const dialogA11y = await new AxeBuilder({ client: browser })
      .setLegacyMode()
      .withTags(["wcag2a", "wcag2aa"])
      .analyze();
    expect(dialogA11y.violations).toEqual([]);
  });
});
