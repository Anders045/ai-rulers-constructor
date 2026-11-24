"""Prepare a fresh synthetic Web task for the standard public-event replay runner."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from scripts.prepare_interop_eval import run, write


ROOT = Path(__file__).resolve().parents[1]
WEB_NAVIGATION_PATCH = ROOT / "scripts/experiments/web-navigation.patch"
PROMPTS = {
    "focus": "工单详情的两个编辑按钮共用一个弹窗。请修好键盘操作：打开时焦点应在输入框，按 Escape 或取消后回到实际打开它的那个按钮。验证改动，不提交。",
    "theme": "把工单详情的“待处理”标签改成项目的信息色。亮色、暗色主题都要能读清，其他状态保持原样。验证改动，不提交。",
}
PROFILE = """# 项目画像

## 项目身份

工单详情是无构建步骤的静态 Web 页面。

## 命令

直接用浏览器打开 index.html。项目的主题契约在 design.md。

## 当前有效事实与约束

| 内容 | 依据类型 | 证据 | 作用域 | 置信度 |
| --- | --- | --- | --- | --- |
| 原生表单与工单编辑弹窗 | observed | index.html | frontend-web | high |
| 主题和对比度约定 | approved | design.md | frontend-web | high |

## 阻塞性未决问题

| 问题 | 重要原因 | 作用域 | 必需审阅人 |
| --- | --- | --- | --- |
"""


@contextmanager
def replay_skill(skill: Path, *, candidate: bool) -> Iterator[Path]:
    """Use the supplied Skill as baseline or apply the experiment to an isolated copy."""
    if not candidate:
        yield skill
        return
    with TemporaryDirectory(prefix="ai-rulers-web-candidate-") as temporary:
        skill_copy = Path(temporary) / "ai-rulers-init"
        shutil.copytree(skill, skill_copy)
        try:
            # TMPDIR may itself live in a Git checkout. Resolve patch paths from the Skill copy.
            run(skill_copy, "git", "--work-tree=.", "apply", "--no-index", "-p3", str(WEB_NAVIGATION_PATCH))
        except RuntimeError as exc:
            raise ValueError(f"Web navigation patch does not apply to Skill {skill}: {exc}") from exc
        yield skill_copy


def prepare(skill: Path, target: Path, prompt: Path, task: str) -> None:
    if target.exists() or prompt.exists() or prompt.is_relative_to(target):
        raise ValueError("Use a fresh target and a fresh prompt path outside it")
    cli = str(skill / "scripts/rulers_init.py")
    registry = json.loads((skill / "templates/domain-registry.json").read_text())["domains"]
    shutil.copytree(ROOT / "tests/fixtures/web-loading", target)
    run(target, "git", "init", "-q", "-b", "main")
    write(target, "README.md", "# 工单详情\n\n用浏览器打开 index.html。主题规范见 design.md。\n")
    write(target, "design.md", "# 主题契约\n\npalette.css 是本项目认可的 Token 来源。"
          "信息状态使用同组的 ink、surface、line 三个 Token。两个主题的普通文本对比度均须达到 4.5:1。\n")
    scratch = ".rulers-work/replay"
    write(target, scratch + "/profile.md", PROFILE)
    app = """# 移动端索引

```yaml
metadata:
  applies_to:
    - mobile/**
  trigger_keywords:
    - mobile
  must_load_with: []
```
"""
    # The stock Web root links to the mobile index. Adopt that navigation target first.
    write(target, scratch + "/app/INDEX.md", app)
    inputs = {"projects": {"workspace": {"profile": scratch + "/profile.md",
              "domains": {"frontend-app": scratch + "/app"}}}}
    write(target, scratch + "/inputs.json", json.dumps(inputs))
    plan = json.loads(run(target, sys.executable, cli, "init-plan", "--project-root", str(target),
                          "--candidates", scratch + "/inputs.json"))
    if plan["status"] != "review":
        raise ValueError(plan["preparation"])
    review = ("--reviewed-by", "synthetic-web-replay", "--evidence", "isolated-evaluation-only")
    run(target, sys.executable, cli, "init-apply", "--plan", plan["plan_path"], *review)

    web = registry["frontend-web"]
    templates = skill / "templates/runtime" / web["template_root"]
    for name in web["templates"]:
        text = (templates / name).read_text().replace("{{RULERS_DIR}}", "documents/rulers")
        text = re.split(r"(?m)^##\s+(?:\d+\.\s+)?AI_FILL", text)[0].rstrip() + "\n"
        write(target, scratch + "/web/" + name, text)
    rule_plan = scratch + "/rules.json"
    run(target, sys.executable, cli, "rules-plan", "--project-root", str(target),
        "--domain", "frontend-web", "--candidate-dir", scratch + "/web", "--reason",
        "Adopt the Web rules for the synthetic project", "--activate-on-apply", "--output", rule_plan)
    run(target, sys.executable, cli, "rules-apply", "--plan", rule_plan, *review)

    # A checkout contains only the files another developer would receive.
    shutil.rmtree(target / ".rulers-work")
    for transient in (".plans", ".transactions"):
        directory = target / "documents/rulers" / transient
        if directory.is_dir():
            shutil.rmtree(directory)
    run(target, "git", "add", ".")
    run(target, "git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "Synthetic Web replay input")
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(PROMPTS[task] + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", type=Path, required=True)
    parser.add_argument("--candidate", action="store_true", help="Apply the Web navigation experiment to a temporary Skill copy")
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--task", choices=PROMPTS, required=True)
    args = parser.parse_args()
    with replay_skill(args.skill.resolve(), candidate=args.candidate) as skill:
        prepare(skill, args.target.resolve(), args.prompt.resolve(), args.task)
