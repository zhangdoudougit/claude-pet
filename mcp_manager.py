"""
mcp_manager.py — MCP 服务器全局配置 (CRUD)

存储: .chat_state/mcp_servers.json (所有项目共享)
格式 (我们的内部格式, 包了一层 enabled):
    {
      "servers": {
        "<name>": {
          "enabled": true,
          "type": "stdio" | "http",
          // stdio:
          "command": "...", "args": [...], "env": {...},
          // http:
          "url": "...", "headers": {...}
        }
      }
    }

启动 claude 时通过 build_effective_mcp_file() 生成符合 MCP 标准的
.chat_state/effective_mcp.json (只含 enabled=true 的 server), 用 --mcp-config 加载。
"""
import json
import os
from pathlib import Path

from PyQt6.QtCore import Qt, QProcess, QProcessEnvironment, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QLineEdit, QPlainTextEdit, QButtonGroup, QRadioButton,
    QCheckBox, QMessageBox, QFrame, QFormLayout, QWidget, QApplication,
    QFileDialog,
)


# ---------------- 导入: 从用户 ~/.claude.json ----------------
def default_claude_json_path() -> Path:
    """跨平台: Windows ~ = %USERPROFILE%; Mac/Linux ~ = $HOME"""
    return Path(os.path.expanduser("~")) / ".claude.json"


def read_claude_json_mcps(path: Path) -> dict:
    """从 ~/.claude.json 提取全局 mcpServers (跳过 projects.*.mcpServers)"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data.get("mcpServers") or {}
    except Exception:
        return {}


def claude_to_internal(name: str, server: dict) -> dict:
    """把标准 MCP 配置转成我们的内部格式 (加 enabled 字段, 推断 type)"""
    s = dict(server) if isinstance(server, dict) else {}
    t = s.get("type")
    if not t:
        # 没显式 type — 有 url 就 http, 否则 stdio
        t = "http" if s.get("url") else "stdio"
    out = {"enabled": True, "type": t}
    if t == "stdio":
        out["command"] = s.get("command", "")
        out["args"] = list(s.get("args") or [])
        out["env"] = dict(s.get("env") or {})
    else:
        out["url"] = s.get("url", "")
        out["headers"] = dict(s.get("headers") or {})
    return out


# ---------------- 测试连接 ----------------
def test_http_server(server: dict) -> tuple[bool, str]:
    """同步测 http: HEAD / GET, 5 秒超时"""
    import urllib.request
    url = server.get("url", "")
    if not url:
        return False, "URL 为空"
    headers = server.get("headers") or {}
    try:
        req = urllib.request.Request(url, headers=headers, method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return True, f"HTTP {resp.status}"
    except Exception as e:
        # HEAD 不支持就退到 GET
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return True, f"HTTP {resp.status} (GET)"
        except Exception as e2:
            return False, f"{type(e2).__name__}: {e2}"


def _state_dir(root: Path) -> Path:
    d = root / ".chat_state"
    d.mkdir(exist_ok=True)
    return d


def mcp_config_file(root: Path) -> Path:
    return _state_dir(root) / "mcp_servers.json"


def effective_mcp_file(root: Path) -> Path:
    return _state_dir(root) / "effective_mcp.json"


# ---------------- 数据层 ----------------
def load_mcp_config(root: Path) -> dict:
    f = mcp_config_file(root)
    if not f.exists():
        return {"servers": {}}
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or "servers" not in d:
            return {"servers": {}}
        return d
    except Exception:
        return {"servers": {}}


def save_mcp_config(root: Path, cfg: dict):
    f = mcp_config_file(root)
    try:
        f.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def build_effective_mcp_file(root: Path) -> Path | None:
    """
    从 mcp_servers.json 生成符合 MCP 标准的 effective_mcp.json (只含 enabled)。
    返回文件路径; 如果没有任何 enabled server, 返回 None (调用方就不该传 --mcp-config)
    """
    cfg = load_mcp_config(root)
    servers = cfg.get("servers") or {}
    out: dict[str, dict] = {}
    for name, s in servers.items():
        if not isinstance(s, dict) or not s.get("enabled"):
            continue
        t = s.get("type", "stdio")
        if t == "stdio":
            entry = {"command": s.get("command", "")}
            args = s.get("args") or []
            if isinstance(args, list) and args:
                entry["args"] = args
            env = s.get("env") or {}
            if isinstance(env, dict) and env:
                entry["env"] = env
            out[name] = entry
        elif t in ("http", "sse"):
            entry = {"type": t, "url": s.get("url", "")}
            headers = s.get("headers") or {}
            if isinstance(headers, dict) and headers:
                entry["headers"] = headers
            out[name] = entry
    if not out:
        return None
    f = effective_mcp_file(root)
    try:
        f.write_text(
            json.dumps({"mcpServers": out}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        return None
    return f


# ---------------- 编辑单个 server 的 dialog ----------------
class MCPEditDialog(QDialog):
    """新增 / 编辑一个 server"""

    def __init__(self, parent=None, name: str = "", server: dict | None = None,
                 existing_names: set[str] = None):
        super().__init__(parent)
        self.setWindowTitle("编辑 MCP 服务器" if name else "新增 MCP 服务器")
        self.setMinimumWidth(520)
        self._existing = existing_names or set()
        self._original_name = name
        s = server or {}
        self._kind = s.get("type", "stdio")

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)

        # 名字
        form_top = QFormLayout()
        self.name_input = QLineEdit(name)
        self.name_input.setPlaceholderText("如 mysql_main / argus / pencil")
        form_top.addRow("名字:", self.name_input)

        # type radio
        type_box = QHBoxLayout()
        self.r_stdio = QRadioButton("stdio (本地进程)")
        self.r_http = QRadioButton("http / sse (远程)")
        if self._kind in ("http", "sse"):
            self.r_http.setChecked(True)
        else:
            self.r_stdio.setChecked(True)
        bg = QButtonGroup(self)
        bg.addButton(self.r_stdio)
        bg.addButton(self.r_http)
        self.r_stdio.toggled.connect(self._refresh_fields)
        type_box.addWidget(self.r_stdio)
        type_box.addWidget(self.r_http)
        type_box.addStretch(1)
        form_top.addRow("类型:", self._wrap(type_box))

        # 启用 checkbox
        self.enabled_cb = QCheckBox("启用 (下次发消息时加载)")
        self.enabled_cb.setChecked(s.get("enabled", True))
        form_top.addRow("", self.enabled_cb)

        v.addLayout(form_top)

        # ---- stdio 字段组 ----
        self.stdio_box = QFrame()
        self.stdio_box.setFrameShape(QFrame.Shape.StyledPanel)
        sf = QFormLayout(self.stdio_box)
        sf.setContentsMargins(10, 8, 10, 10)
        self.cmd_input = QLineEdit(s.get("command", ""))
        self.cmd_input.setPlaceholderText("如 node / python / npx")
        sf.addRow("命令:", self.cmd_input)
        self.args_text = QPlainTextEdit("\n".join(s.get("args") or []))
        self.args_text.setPlaceholderText("一行一个参数")
        self.args_text.setFixedHeight(70)
        sf.addRow("参数:", self.args_text)
        env_lines = [
            f"{k}={v}" for k, v in (s.get("env") or {}).items()
        ]
        self.env_text = QPlainTextEdit("\n".join(env_lines))
        self.env_text.setPlaceholderText("KEY=VALUE 一行一个")
        self.env_text.setFixedHeight(70)
        sf.addRow("环境变量:", self.env_text)
        v.addWidget(self.stdio_box)

        # ---- http 字段组 ----
        self.http_box = QFrame()
        self.http_box.setFrameShape(QFrame.Shape.StyledPanel)
        hf = QFormLayout(self.http_box)
        hf.setContentsMargins(10, 8, 10, 10)
        self.url_input = QLineEdit(s.get("url", ""))
        self.url_input.setPlaceholderText("https://...")
        hf.addRow("URL:", self.url_input)
        hdr_lines = [f"{k}={v}" for k, v in (s.get("headers") or {}).items()]
        self.headers_text = QPlainTextEdit("\n".join(hdr_lines))
        self.headers_text.setPlaceholderText("KEY=VALUE 一行一个 (如 Authorization=Bearer xxx)")
        self.headers_text.setFixedHeight(70)
        hf.addRow("Headers:", self.headers_text)
        v.addWidget(self.http_box)

        self._refresh_fields()

        # 按钮
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        save = QPushButton("保存")
        save.setDefault(True)
        save.setStyleSheet(
            "background:#07c160; color:white; padding:6px 18px; "
            "border-radius:4px; border:0; font-weight:bold;"
        )
        save.clicked.connect(self._on_save)
        btns.addWidget(save)
        v.addLayout(btns)

    def _wrap(self, layout) -> QWidget:
        w = QWidget()
        w.setLayout(layout)
        return w

    def _refresh_fields(self):
        is_stdio = self.r_stdio.isChecked()
        self.stdio_box.setVisible(is_stdio)
        self.http_box.setVisible(not is_stdio)

    def _on_save(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "缺名字", "请填一个名字")
            return
        if name != self._original_name and name in self._existing:
            QMessageBox.warning(self, "重名", f"已经有叫 [{name}] 的 server 了")
            return
        self.accept()

    def result_data(self) -> tuple[str, dict]:
        name = self.name_input.text().strip()
        if self.r_stdio.isChecked():
            args = [
                ln.strip() for ln in self.args_text.toPlainText().splitlines()
                if ln.strip()
            ]
            env: dict[str, str] = {}
            for ln in self.env_text.toPlainText().splitlines():
                ln = ln.strip()
                if not ln or "=" not in ln:
                    continue
                k, _, val = ln.partition("=")
                env[k.strip()] = val.strip()
            data = {
                "enabled": self.enabled_cb.isChecked(),
                "type": "stdio",
                "command": self.cmd_input.text().strip(),
                "args": args,
                "env": env,
            }
        else:
            headers: dict[str, str] = {}
            for ln in self.headers_text.toPlainText().splitlines():
                ln = ln.strip()
                if not ln or "=" not in ln:
                    continue
                k, _, val = ln.partition("=")
                headers[k.strip()] = val.strip()
            data = {
                "enabled": self.enabled_cb.isChecked(),
                "type": "http",
                "url": self.url_input.text().strip(),
                "headers": headers,
            }
        return name, data


# ---------------- 主管理 dialog ----------------
class MCPManagerWidget(QWidget):
    """MCP server 列表 + 增删改 (无关闭按钮, 可嵌入 dialog/tab)."""

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self._root = root

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # 列表
        self.list_w = QListWidget()
        self.list_w.itemDoubleClicked.connect(lambda _: self._on_edit())
        self.list_w.itemChanged.connect(self._on_item_changed)
        v.addWidget(self.list_w, 1)

        # 操作按钮
        btn_row = QHBoxLayout()
        add = QPushButton("➕ 新增")
        add.clicked.connect(self._on_add)
        btn_row.addWidget(add)
        edit = QPushButton("✏ 编辑")
        edit.clicked.connect(self._on_edit)
        btn_row.addWidget(edit)
        rm = QPushButton("🗑 删除")
        rm.clicked.connect(self._on_remove)
        btn_row.addWidget(rm)
        test = QPushButton("🔬 测试连接")
        test.clicked.connect(self._on_test)
        btn_row.addWidget(test)
        btn_row.addStretch(1)
        imp = QPushButton("📥 从 ~/.claude.json 导入")
        imp.setToolTip("把你 Claude Code 全局配置里的 mcpServers 拉进来")
        imp.clicked.connect(self._on_import)
        btn_row.addWidget(imp)
        v.addLayout(btn_row)

        self._load_into_list()

    # ---- 渲染 ----
    def _load_into_list(self):
        self.list_w.blockSignals(True)
        self.list_w.clear()
        cfg = load_mcp_config(self._root)
        for name, s in (cfg.get("servers") or {}).items():
            self.list_w.addItem(self._make_item(name, s))
        self.list_w.blockSignals(False)

    def _make_item(self, name: str, s: dict) -> QListWidgetItem:
        t = s.get("type", "stdio")
        if t == "stdio":
            summary = s.get("command", "?")
            args = s.get("args") or []
            if args:
                summary += " " + " ".join(args[:2])
                if len(args) > 2:
                    summary += " …"
        else:
            summary = s.get("url", "?")
        item = QListWidgetItem(f"[{t}] {name}  ·  {summary}")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if s.get("enabled") else Qt.CheckState.Unchecked
        )
        item.setData(Qt.ItemDataRole.UserRole, name)
        return item

    # ---- 动作 ----
    def _existing_names(self) -> set[str]:
        cfg = load_mcp_config(self._root)
        return set((cfg.get("servers") or {}).keys())

    def _on_add(self):
        dlg = MCPEditDialog(self, name="", server=None,
                            existing_names=self._existing_names())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, data = dlg.result_data()
        cfg = load_mcp_config(self._root)
        cfg.setdefault("servers", {})[name] = data
        save_mcp_config(self._root, cfg)
        self._load_into_list()

    def _on_edit(self):
        item = self.list_w.currentItem()
        if item is None:
            QMessageBox.information(self, "选一个", "先选中一行")
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        cfg = load_mcp_config(self._root)
        existing_other = set(cfg.get("servers", {}).keys()) - {name}
        dlg = MCPEditDialog(self, name=name,
                            server=cfg.get("servers", {}).get(name),
                            existing_names=existing_other)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_name, data = dlg.result_data()
        if new_name != name:
            cfg["servers"].pop(name, None)
        cfg.setdefault("servers", {})[new_name] = data
        save_mcp_config(self._root, cfg)
        self._load_into_list()

    def _on_remove(self):
        item = self.list_w.currentItem()
        if item is None:
            QMessageBox.information(self, "选一个", "先选中一行")
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        ret = QMessageBox.question(
            self, "确认删除",
            f"删除 [{name}]?\n这条记录会从配置里移除, 没法撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        cfg = load_mcp_config(self._root)
        cfg.get("servers", {}).pop(name, None)
        save_mcp_config(self._root, cfg)
        self._load_into_list()

    def _on_item_changed(self, item: QListWidgetItem):
        """checkbox 切换 = 启用/禁用"""
        name = item.data(Qt.ItemDataRole.UserRole)
        if name is None:
            return
        cfg = load_mcp_config(self._root)
        s = cfg.get("servers", {}).get(name)
        if s is None:
            return
        s["enabled"] = item.checkState() == Qt.CheckState.Checked
        save_mcp_config(self._root, cfg)

    # ---- 测试连接 ----
    def _on_test(self):
        item = self.list_w.currentItem()
        if item is None:
            QMessageBox.information(self, "选一个", "先选中一行再测试")
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        cfg = load_mcp_config(self._root)
        s = cfg.get("servers", {}).get(name)
        if s is None:
            return
        t = s.get("type", "stdio")
        if t == "stdio":
            self._test_stdio(name, s)
        else:
            self._test_http(name, s)

    def _test_http(self, name: str, s: dict):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        ok, msg = test_http_server(s)
        QApplication.restoreOverrideCursor()
        if ok:
            QMessageBox.information(self, "✓ 通过", f"[{name}] {msg}")
        else:
            QMessageBox.warning(self, "✗ 失败", f"[{name}]\n{msg}")

    def _test_stdio(self, name: str, s: dict):
        """启动进程, 等 2.5 秒看是否还活着 (MCP server 会等 stdin 不退出)。
        信号驱动 + exec() 模态, 让事件循环正常转, 不会卡死。"""
        cmd = s.get("command", "")
        args = list(s.get("args") or [])
        env = s.get("env") or {}
        if not cmd:
            QMessageBox.warning(self, "✗ 失败", f"[{name}] 没填命令")
            return

        proc = QProcess(self)
        qenv = QProcessEnvironment.systemEnvironment()
        for k, v in env.items():
            qenv.insert(str(k), str(v))
        proc.setProcessEnvironment(qenv)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)

        prog = QMessageBox(self)
        prog.setWindowTitle("测试中")
        prog.setIcon(QMessageBox.Icon.Information)
        prog.setText(
            f"启动 [{name}] 中, 等 2.5 秒看是否能稳定运行...\n\n"
            f"命令: {cmd} {' '.join(args)[:80]}"
        )
        # 加 Cancel 按钮, 卡住时豆哥也能跳出
        prog.setStandardButtons(QMessageBox.StandardButton.Cancel)

        # 共享状态: 谁先到谁定胜负
        state = {"resolved": False, "result": None}  # result: (ok, title, msg)

        def resolve(ok: bool, title: str, msg: str):
            if state["resolved"]:
                return
            state["resolved"] = True
            state["result"] = (ok, title, msg)
            # kill 进程, 关 progress, exec() 会返回
            try:
                if proc.state() != QProcess.ProcessState.NotRunning:
                    proc.kill()
                    proc.waitForFinished(500)
            except Exception: pass
            prog.done(0)

        def on_finished(_code, _status):
            # 2.5 秒内退出 = 失败
            stderr = bytes(proc.readAllStandardError()).decode(
                "utf-8", errors="replace"
            )[:600]
            stdout = bytes(proc.readAllStandardOutput()).decode(
                "utf-8", errors="replace"
            )[:300]
            detail = stderr or stdout or "(无输出)"
            resolve(False, "✗ 失败",
                    f"[{name}] 进程提前退出 (码 {proc.exitCode()})\n\n{detail}")

        def on_error(err):
            resolve(False, "✗ 失败",
                    f"[{name}] 启动失败 ({err})\n"
                    f"命令: {cmd} {' '.join(args)}\n"
                    f"系统找不到这个可执行文件?")

        def on_timeout():
            if proc.state() == QProcess.ProcessState.Running:
                resolve(True, "✓ 通过",
                        f"[{name}] 启动正常 (2.5 秒内未退出)\n\n"
                        "注: 这只验证了进程能起来, 不等于 MCP 协议握手成功。")
            # else: on_finished / on_error 已经处理

        def on_cancel(_btn):
            resolve(False, "✗ 已取消", f"[{name}] 用户取消了测试")

        proc.finished.connect(on_finished)
        proc.errorOccurred.connect(on_error)
        prog.buttonClicked.connect(on_cancel)

        proc.start(cmd, args)
        QTimer.singleShot(2500, on_timeout)

        # 模态 — 阻塞当前函数, 但 Qt 事件循环正常跑, 信号能触发
        prog.exec()

        # exec 返回后弹结果
        if state["result"]:
            ok, title, msg = state["result"]
            (QMessageBox.information if ok else QMessageBox.warning)(
                self, title, msg
            )

    # ---- 导入 ----
    def _on_import(self):
        default = default_claude_json_path()
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Claude Code 配置文件",
            str(default),
            "JSON (*.json);;所有文件 (*)",
        )
        if not path:
            return
        src_mcps = read_claude_json_mcps(Path(path))
        if not src_mcps:
            QMessageBox.information(
                self, "啥都没找到",
                f"{path}\n里没有 mcpServers 字段, 或解析失败。"
            )
            return

        cfg = load_mcp_config(self._root)
        cfg.setdefault("servers", {})
        existing = set(cfg["servers"].keys())
        new_names = [n for n in src_mcps.keys() if n not in existing]
        dup_names = [n for n in src_mcps.keys() if n in existing]

        # 有重名的, 问豆哥怎么办
        overwrite = False
        if dup_names:
            ret = QMessageBox.question(
                self, "已有同名 server",
                f"已存在: {', '.join(dup_names)}\n\n"
                "覆盖这些?\n"
                "  [Yes] 覆盖现有的 (保留 enabled 状态)\n"
                "  [No]  跳过, 只导入新的 ({n} 个)\n"
                "  [Cancel] 取消整个导入".format(n=len(new_names)),
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No,
            )
            if ret == QMessageBox.StandardButton.Cancel:
                return
            overwrite = ret == QMessageBox.StandardButton.Yes

        added, replaced, skipped = 0, 0, 0
        for n, s in src_mcps.items():
            internal = claude_to_internal(n, s)
            if n in existing:
                if overwrite:
                    # 保留原 enabled 状态
                    old_enabled = cfg["servers"][n].get("enabled", True)
                    internal["enabled"] = old_enabled
                    cfg["servers"][n] = internal
                    replaced += 1
                else:
                    skipped += 1
            else:
                cfg["servers"][n] = internal
                added += 1

        save_mcp_config(self._root, cfg)
        self._load_into_list()
        QMessageBox.information(
            self, "导入完成",
            f"新增: {added}    覆盖: {replaced}    跳过: {skipped}\n"
            f"来源: {path}"
        )


class MCPManagerDialog(QDialog):
    """老版兼容 wrapper — QDialog 包 MCPManagerWidget + 关闭按钮.

    新 ChatWebWindow 的 SettingsDialog 直接嵌入 MCPManagerWidget;
    老 ChatWindow 的右键菜单还是用这个 dialog.
    """

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MCP 服务器")
        self.setMinimumSize(560, 400)

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)

        v.addWidget(QLabel(
            "全局 MCP 服务器配置 — 所有项目和闲聊共享。\n"
            "勾选 = 启用 (下次发消息时通过 claude --mcp-config 加载),\n"
            "取消勾选 = 禁用 (保留配置但不加载)。"
        ))
        self.widget = MCPManagerWidget(root, self)
        v.addWidget(self.widget, 1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        bar.addWidget(close)
        v.addLayout(bar)


# ---------------- 单跑测试 ----------------
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    root = Path(__file__).parent
    dlg = MCPManagerDialog(root)
    dlg.exec()
