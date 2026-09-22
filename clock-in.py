# -*- coding: utf-8 -*-

import datetime
import os
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


WEB_URL = "https://healthreport.zju.edu.cn/ncov/wap/default/index"


class ClockInError(Exception):
    pass


def has_old_info(driver):
    return bool(
        driver.execute_script(
            "return Boolean(window.vm && window.vm.oldInfo);"
        )
    )


def visible_error(driver):
    values = driver.execute_script(
        """
        return [
          document.getElementById('msg')?.textContent || '',
          document.querySelector('.wapat-title')?.textContent || '',
          document.querySelector('.wapcf-title')?.textContent || ''
        ];
        """
    )
    return " ".join(str(value).strip() for value in values if value).strip()


def recognize_verify_code(driver):
    images = driver.find_elements(By.CSS_SELECTOR, "img")
    candidates = [
        image for image in images
        if image.is_displayed()
        and (
            "code" in (image.get_attribute("src") or "").lower()
            or "verify" in (image.get_attribute("src") or "").lower()
        )
    ]
    if not candidates:
        candidates = [image for image in images if image.is_displayed()]
    if not candidates:
        return ""

    image_bytes = candidates[0].screenshot_as_png
    result = subprocess.run(
        [
            "tesseract",
            "stdin",
            "stdout",
            "-l",
            "eng",
            "--psm",
            "7",
            "-c",
            "tessedit_char_whitelist="
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
        ],
        input=image_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.stdout.decode("utf-8", errors="replace").strip()


def submit_form(driver, wait):
    for attempt in range(3):
        driver.execute_script(
            """
            const vm = window.vm;
            for (const key in vm.oldInfo) {
              if (vm.oldInfo[key]) vm.info[key] = vm.oldInfo[key];
            }
            vm.confirm();
            document.querySelector('.wapcf-btn-ok')?.click();
            """
        )
        time.sleep(1)

        state = driver.execute_script(
            """
            return {
              success: Boolean(window.vm && window.vm.show),
              error: [
                document.querySelector('.wapat-title')?.textContent || '',
                document.querySelector('.wapcf-title')?.textContent || ''
              ].join(' ').trim()
            };
            """
        )
        if state["success"]:
            return "已为您打卡成功！"

        error = state["error"] or visible_error(driver)
        if "每天只能填报一次" in error or "已经填报" in error:
            return "今日已经打卡，无需重复提交。"

        if "验证码" in error:
            code = recognize_verify_code(driver)
            if code:
                driver.execute_script(
                    """
                    window.vm.info.verifyCode = arguments[0];
                    window.vm.confirm();
                    document.querySelector('.wapcf-btn-ok')?.click();
                    """,
                    code,
                )
                time.sleep(1)
                continue

        if attempt < 2 and not error:
            time.sleep(2)
            continue
        raise ClockInError(error or "提交后页面未显示成功状态")

    raise ClockInError("验证码识别失败，已达到最大重试次数")


def run_check_in(username, password):
    options = Options()
    options.page_load_strategy = "none"
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,1200")
    options.add_argument("--lang=zh-CN")

    driver = None
    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(90)
        wait = WebDriverWait(driver, 90)

        print("打开浙大健康上报页面...")
        driver.get(WEB_URL)

        if not has_old_info(driver):
            username_input = wait.until(
                EC.presence_of_element_located((By.ID, "username"))
            )
            password_input = wait.until(
                EC.presence_of_element_located((By.ID, "password"))
            )
            username_input.clear()
            username_input.send_keys(username)
            password_input.clear()
            password_input.send_keys(password)

            login_button = wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, ".login-button > button")
                )
            )
            login_button.click()

            try:
                wait.until(lambda current: has_old_info(current))
            except TimeoutException as error:
                message = visible_error(driver)
                raise ClockInError(
                    message or "登录后未加载到健康上报表单"
                ) from error

        print("已登录到浙大健康上报页面")
        print("正在提交打卡...")
        return submit_form(driver, wait)
    except WebDriverException as error:
        raise ClockInError(f"浏览器运行失败：{error}") from error
    finally:
        if driver is not None:
            driver.quit()


def main(username, password):
    now = datetime.datetime.now(ZoneInfo("Asia/Shanghai"))
    print(f"\n[Time] {now:%Y-%m-%d %H:%M:%S}")
    print("🚌 打卡任务启动")
    print(run_check_in(username, password))


if __name__ == "__main__":
    username = os.environ.get("ACCOUNT")
    password = os.environ.get("PASSWORD")

    if not username and len(sys.argv) > 1:
        username = sys.argv[1]
    if not password and len(sys.argv) > 2:
        password = sys.argv[2]

    if not username or not password:
        print("请在仓库 Secrets 中配置 ACCOUNT 和 PASSWORD")
        sys.exit(2)

    try:
        main(username, password)
    except Exception as error:
        print(f"打卡失败：{error}")
        sys.exit(1)
