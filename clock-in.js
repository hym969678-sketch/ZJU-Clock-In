const puppeteer = require("puppeteer-core");
const { Launcher } = require("chrome-launcher");
const { execFileSync } = require("child_process");

const WEB_URL = "https://healthreport.zju.edu.cn/ncov/wap/default/index";
const username = process.env.ACCOUNT;
const password = process.env.PASSWORD;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pageError(page) {
  return page.evaluate(() => {
    const selectors = ["#msg", ".wapat-title", ".wapcf-title"];
    return selectors
      .flatMap((selector) =>
        Array.from(document.querySelectorAll(selector)).map((element) =>
          (element.textContent || "").trim()
        )
      )
      .filter(Boolean)
      .join(" ");
  }).catch(() => "");
}

async function recognizeVerifyCode(page) {
  const image = await page.$('img[src*="code"], img[src*="verify"]');
  if (!image) return "";

  const imageBytes = await image.screenshot({ type: "png" });
  let output = "";
  try {
    output = execFileSync(
      "tesseract",
      [
        "stdin",
        "stdout",
        "-l",
        "eng",
        "--psm",
        "7",
        "-c",
        "tessedit_char_whitelist=abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
      ],
      { input: imageBytes, encoding: "utf8" }
    );
  } catch {
    return "";
  }
  return output.trim();
}

async function submit(page) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await page.evaluate(() => {
      const vm = window.vm;
      for (const key in vm.oldInfo) {
        if (vm.oldInfo[key]) vm.info[key] = vm.oldInfo[key];
      }
      vm.confirm();
      document.querySelector(".wapcf-btn-ok")?.click();
    });
    await sleep(1000);

    const state = await page.evaluate(() => ({
      success: Boolean(window.vm && window.vm.show),
      error: [
        document.querySelector(".wapat-title")?.textContent || "",
        document.querySelector(".wapcf-title")?.textContent || "",
      ].join(" ").trim(),
    }));

    if (state.success) return "已为您打卡成功！";
    if (state.error.includes("每天只能填报一次") || state.error.includes("已经填报")) {
      return "今日已经打卡，无需重复提交。";
    }

    if (state.error.includes("验证码")) {
      const code = await recognizeVerifyCode(page);
      if (code) {
        await page.evaluate((value) => {
          window.vm.info.verifyCode = value;
          window.vm.confirm();
          document.querySelector(".wapcf-btn-ok")?.click();
        }, code);
        await sleep(1000);
        continue;
      }
    }

    const error = state.error || await pageError(page);
    throw new Error(error || "提交后页面未显示成功状态");
  }
  throw new Error("验证码识别失败，已达到最大重试次数");
}

async function main() {
  if (!username || !password) {
    throw new Error("请在仓库 Secrets 中配置 ACCOUNT 和 PASSWORD");
  }

  const executablePath = Launcher.getInstallations()[0] || "/usr/bin/google-chrome";
  const browser = await puppeteer.launch({
    executablePath,
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
  });

  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 1200 });
    await page.setUserAgent(
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 " +
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    );
    page.setDefaultNavigationTimeout(90000);
    page.setDefaultTimeout(60000);

    console.log("打开浙大健康上报页面...");
    try {
      await page.goto(WEB_URL, {
        waitUntil: "domcontentloaded",
        timeout: 20000,
      });
      console.log("页面 DOM 已加载，等待前端初始化...");
    } catch (error) {
      if (!String(error?.message || error).includes("Navigation timeout")) {
        throw error;
      }
      console.log("页面仍在加载，继续使用当前文档...");
    }

    await page.waitForFunction(
      () => Boolean(document.querySelector("#username") || window.vm?.oldInfo)
    );

    if (await page.$("#username")) {
      await page.waitForSelector("#username", { visible: true });
      await page.waitForSelector("#password", { visible: true });
      await page.click("#username");
      await page.type("#username", username);
      await page.click("#password");
      await page.type("#password", password);
      await page.click(".login-button > button");
      await page.waitForFunction(() => Boolean(window.vm?.oldInfo));
    }

    console.log("已登录到浙大健康上报页面");
    console.log("正在提交打卡...");
    console.log(await submit(page));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error("打卡失败：" + (error?.message || error));
  process.exitCode = 1;
});
