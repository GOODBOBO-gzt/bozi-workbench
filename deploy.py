#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deploy local files to GOODBOBO-gzt/bozi-workbench (master) via GitHub Git Data API.

为什么用 Git Data API（而不是 gh api 逐文件 PUT，也不是 git push）：
- 本机环境 git 走 HTTPS 连不上 github.com:443（curl 28 超时），只有 `gh api` 能通。
- 旧的 deploy.py 对 5 个文件各发一次 Contents API PUT = 5 个独立 commit = 触发 5 次
  GitHub Pages 构建（前几次被取消/跳过，末端那次才带上全部文件），是线上卡旧版的根因。
- 本脚本改用 Git Data API：建 5 个 blob -> 一棵 tree -> 1 个 commit -> 更新 master 引用，
  一次部署 = 1 个 commit = 1 次 Pages 构建，干净且不会再被自己取消。
- 同时删除了仓库里冗余的 fund-daily.yml Actions（它用 git push 抢 master，是另一个冲突源），
  现在 WorkBuddy 自动化是唯一的推送方。
"""
import base64
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "GOODBOBO-gzt/bozi-workbench"
BRANCH = "master"
MSG = "chore: 同步本地改动到线上（含每日基金实时数据）"

# 需要同步的文件（fund-daily.yml 已删除，不再推送）
FILES = [
    "index.html",
    "fund-basis.json",
    "fund_update.py",
    "fund-live.json",
]


def gh_api(method, path, body=None, retries=3):
    cmd = ["gh", "api", "--method", method, "repos/%s/%s" % (REPO, path)]
    tmpname = None
    if body is not None:
        tf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(body, tf, ensure_ascii=False)
        tf.close()
        tmpname = tf.name
        cmd += ["--input", tmpname]
    last_err = ""
    for _ in range(retries):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if tmpname:
            try:
                os.unlink(tmpname)
            except OSError:
                pass
        if r.returncode == 0 and r.stdout.strip():
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                last_err = "bad json: " + r.stdout[:200]
                continue
        last_err = r.stderr.strip()[:300]
        # 抖动后重试
        subprocess.run(["python", "-c", "import time;time.sleep(2)"])
    raise RuntimeError("gh api %s %s 失败: %s" % (method, path, last_err))


def main():
    # 生成本地离线快照（双击即可看，零网络依赖；产物不推送，仅本地留档）
    try:
        subprocess.run([sys.executable, "build_offline.py"], cwd=HERE, check=False)
    except Exception as e:
        print("build_offline 跳过:", e)

    # 1) 取 master 当前引用与 tree
    print(">>> 读取 master 当前 commit ...")
    ref = gh_api("GET", "git/refs/heads/%s" % BRANCH)
    base_commit_sha = ref["object"]["sha"]
    base_commit = gh_api("GET", "git/commits/%s" % base_commit_sha)
    base_tree_sha = base_commit["tree"]["sha"]

    # 2) 为每个文件建 blob
    print(">>> 创建 blobs ...")
    tree_entries = []
    for rel in FILES:
        lp = os.path.join(HERE, rel)
        if not os.path.exists(lp):
            print("MISSING local", rel)
            continue
        with open(lp, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode("ascii")
        blob = gh_api("POST", "git/blobs", {"content": content_b64, "encoding": "base64"})
        tree_entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print("  blob ok:", rel)

    # 3) 建 tree（以当前 tree 为 base，覆盖上述文件）
    print(">>> 创建 tree ...")
    new_tree = gh_api("POST", "git/trees", {"base_tree": base_tree_sha, "tree": tree_entries})
    new_tree_sha = new_tree["sha"]

    # 4) 建 commit
    print(">>> 创建 commit ...")
    new_commit = gh_api("POST", "git/commits",
                        {"message": MSG, "tree": new_tree_sha, "parents": [base_commit_sha]})
    new_commit_sha = new_commit["sha"]

    # 5) 更新 master 引用
    print(">>> 更新 %s 引用 ..." % BRANCH)
    gh_api("PATCH", "git/refs/heads/%s" % BRANCH, {"sha": new_commit_sha, "force": False})

    print("OK commit", new_commit_sha)
    print("DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("DEPLOY FAIL:", e)
        sys.exit(1)
