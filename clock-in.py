# -*- coding: utf-8 -*-

# 打卡脚修改自ZJU-nCov-Hitcarder的开源代码，感谢这位同学开源的代码

import requests
import json
import re
import datetime
import os
import time
import sys
from urllib.parse import quote
from zoneinfo import ZoneInfo


class DaKa(object):
    """Hit card class

    Attributes:
        username: (str) 浙大统一认证平台用户名（一般为学号）
        password: (str) 浙大统一认证平台密码
        login_url: (str) 登录url
        base_url: (str) 打卡首页url
        save_url: (str) 提交打卡url
        self.headers: (dir) 请求头
        sess: (requests.Session) 统一的session
    """

    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.login_url = "https://zjuam.zju.edu.cn/cas/login?service=http%3A%2F%2Fservice.zju.edu.cn%2F"
        self.health_redirect_url = (
            "https://zjuam.zju.edu.cn/cas/login?service="
            "https%3A%2F%2Fhealthreport.zju.edu.cn%2Fa_zju%2Fapi%2Fsso%2Findex"
            "%3Fredirect%3Dhttps%253A%252F%252Fhealthreport.zju.edu.cn"
            "%252Fncov%252Fwap%252Fdefault%252Findex%26from%3Dwap"
        )
        self.base_url = "https://healthreport.zju.edu.cn/ncov/wap/default/index"        self.save_url = "https://healthreport.zju.edu.cn/ncov/wap/default/save"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/75.0.3770.100 Safari/537.36"
        }
        self.sess = requests.Session()

    def login(self):
        """Login to ZJU platform."""
        res = self.sess.get(self.login_url, headers=self.headers, timeout=30)
        res.raise_for_status()

        execution_match = re.search(
            r'name=["\']execution["\'][^>]*value=["\']([^"\']+)["\']',
            res.text,
            re.IGNORECASE,
        )
        if execution_match is None:
            execution_match = re.search(
                r'value=["\']([^"\']+)["\'][^>]*name=["\']execution["\']',
                res.text,
                re.IGNORECASE,
            )
        if execution_match is None:
            raise LoginError(
                "统一身份认证页面未找到 execution 参数，"
                f"status={res.status_code}, url={res.url}"
            )
        execution = execution_match.group(1)

        pubkey = self.sess.get(
            "https://zjuam.zju.edu.cn/cas/v2/getPubKey",
            headers=self.headers,
            timeout=30,
        )
        pubkey.raise_for_status()
        key_data = pubkey.json()
        encrypt_password = self._rsa_encrypt(
            self.password, key_data["exponent"], key_data["modulus"]
        )

        data = {
            "username": self.username,
            "password": encrypt_password,
            "execution": execution,
            "_eventId": "submit",
            "authcode": "",
        }
        post_headers = dict(self.headers)
        post_headers["Content-Type"] = "application/x-www-form-urlencoded"
        res = self.sess.post(
            self.login_url,
            data=data,
            headers=post_headers,
            allow_redirects=False,
            timeout=30,
        )

        if res.status_code not in (301, 302, 303, 307, 308):
            message = re.search(
                r'<span[^>]+id=["\']msg["\'][^>]*>(.*?)</span>',
                res.text,
                re.IGNORECASE | re.DOTALL,
            )
            detail = re.sub(r"\s+", " ", message.group(1)).strip() if message else (
                f"HTTP {res.status_code}"
            )
            raise LoginError(f"统一身份认证失败：{detail}")

        redirect_res = self.sess.get(
            self.health_redirect_url,
            headers=self.headers,
            allow_redirects=True,
            timeout=30,
        )
        if redirect_res.status_code >= 400:
            raise LoginError(
                "健康上报服务跳转失败："
                f"HTTP {redirect_res.status_code}, url={redirect_res.url}"
            )
        if "zjuam.zju.edu.cn/cas/login" in redirect_res.url:
            raise LoginError("统一身份认证成功后未能跳转到健康上报服务")

        return self.sess

    def post(self):
        """Post the hitcard info."""
        res = self.sess.post(
            self.save_url,
            data=self.info,
            headers=self.headers,
            timeout=30,
        )
        res.raise_for_status()
        return res.json()

    def get_date(self):
        """Get current date in China Standard Time."""
        today = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).date()
        return today.strftime("%Y%m%d")

    def get_info(self, html=None):
        """Get hitcard info, which is the old info with updated new time."""
        if not html:
            res = self.sess.get(self.base_url, headers=self.headers, timeout=30)
            res.raise_for_status()
            html = res.content.decode(res.encoding or "utf-8", errors="replace")

        try:
            old_infos = re.findall(r'oldInfo\s*:\s*(\{[^\r\n]+\})', html)
            if len(old_infos) != 0:
                old_info = json.loads(old_infos[0])
            else:
                raise RegexMatchError("未发现缓存信息，请先至少手动成功打卡一次再运行脚本")

            default_infos = re.findall(r'def\s*=\s*(\{[^\r\n]+\})', html)
            if not default_infos:
                raise RegexMatchError("未发现默认打卡信息")
            new_info_tmp = json.loads(default_infos[0])
            new_id = new_info_tmp['id']
            name = re.findall(r'realname\s*:\s*"([^"\]+)"', html)[0]
            number = re.findall(r"number\s*:\s*'([^'\]+)'", html)[0]
        except IndexError:
            raise RegexMatchError('Relative info not found in html with regex')
        except json.decoder.JSONDecodeError:
            raise DecodeError('JSON decode error')

        new_info = old_info.copy()
        new_info['id'] = new_id
        new_info['name'] = name
        new_info['number'] = number
        new_info["date"] = self.get_date()
        new_info["created"] = round(time.time())
        new_info["address"] = "浙江省杭州市西湖区"
        new_info["area"] = "浙江省 杭州市 西湖区"
        new_info["province"] = new_info["area"].split(' ')[0]
        new_info["city"] = new_info["area"].split(' ')[1]
        # form change
        new_info['jrdqtlqk[]'] = 0
        new_info['jrdqjcqk[]'] = 0
        new_info['sfsqhzjkk'] = 1   # 是否申领杭州健康码
        new_info['sqhzjkkys'] = 1   # 杭州健康吗颜色，1:绿色 2:红色 3:黄色
        new_info['sfqrxxss'] = 1    # 是否确认信息属实
        new_info['jcqzrq'] = ""
        new_info['gwszdd'] = ""
        new_info['szgjcs'] = ""
        self.info = new_info
        return new_info

    def _rsa_encrypt(self, password_str, e_str, M_str):
        password_bytes = bytes(password_str, 'ascii')
        password_int = int.from_bytes(password_bytes, 'big')
        e_int = int(e_str, 16)
        M_int = int(M_str, 16)
        result_int = pow(password_int, e_int, M_int)
        return hex(result_int)[2:].rjust(128, '0')


# Exceptions
class LoginError(Exception):
    """Login Exception"""
    pass


class RegexMatchError(Exception):
    """Regex Matching Exception"""
    pass


class DecodeError(Exception):
    """JSON Decode Exception"""
    pass


def main(username, password):
    """Run the complete check-in process."""
    print(
        "\n[Time] %s"
        % datetime.datetime.now(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )
    print("🚌 打卡任务启动")

    dk = DaKa(username, password)

    print("登录到浙大统一身份认证平台...")
    dk.login()
    print("已登录到浙大统一身份认证平台")

    print("正在获取个人信息...")
    dk.get_info()
    print("已成功获取个人信息")

    print("正在提交打卡...")
    result = dk.post()
    if str(result.get("e")) != "0":
        raise RuntimeError(
            result.get("m") or json.dumps(result, ensure_ascii=False)
        )
    print("已为您打卡成功！")


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
    except Exception as err:
        print(f"打卡失败：{err}")
        sys.exit(1)
