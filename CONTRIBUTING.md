# 给泡沫提 PR

非常欢迎,谢谢你愿意花时间。

## 开发环境

- Python 3.10+
- Windows 10+ / macOS / Linux(Mica 仅 Win11,其他平台自动降级)
- [Claude Code](https://docs.claude.com/en/docs/claude-code/quickstart) 已登录

```bash
git clone https://github.com/zhangdoudougit/foamo-pet.git
cd foamo-pet
pip install -r requirements.txt
python foamo_pet.py
```

调试聊天面板可以单跑,免去启 foamo_pet 主进程:

```bash
python chat_window.py    # 一个假桌宠 + 真面板, 用来验证布局/拖拉/markdown
```

## 提 Issue

### Bug
请贴:
1. 平台(Windows 11 / macOS 14 / ...)+ Python 版本
2. `.chat_state/debug.log` 末尾若干行
3. `.chat_state/permission.log`(如果跟权限相关)
4. 复现步骤

### Feature
说清楚**场景**比"建议加 X 功能"更有用——你想解决的真实问题是什么。

## 提 PR

1. Fork → branch → 改 → push → PR
2. 一个 PR 解决一件事,不要把"修 bug + 加功能 + 重构"塞一起
3. 改了行为麻烦顺手更新 `CHANGELOG.md` 的 `[Unreleased]` 段
4. 改了用户可见的东西更新 `README.md`

## 代码风格

- 4 空格缩进(沿用现有)
- 中英文混合注释 OK,**不写废话注释**(代码已经说清楚了就别再描述一遍)
- 注释只说"为什么这么写",不说"这行做了什么"
- 函数 50 行以上考虑拆
- 不引新依赖除非真的需要(目前只 PyQt6)

## 测试

没有自动化测试套件,改完务必至少手动验证:

- `start.bat`(或 `python foamo_pet.py`)起来
- 双击桌宠开聊天面板
- 发一条消息验证收回复
- 切项目模式验证 session 隔离
- 拖角调大小、滚动条能拖

CI 会跑 `py_compile` 语法检查,改完先在本地确认:

```bash
python -m py_compile foamo_pet.py chat_window.py permission_dialog.py context.py journal.py
```

## 行为准则

对豆哥 / 泡沫尊重就行。技术讨论 OK 锐利,人身攻击不行。

---

_本泡沫等着审你的 PR_
