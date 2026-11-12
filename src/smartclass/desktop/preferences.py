"""在本地窗口编辑三种模型服务与每日课程清单。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLineEdit,
    QMessageBox, QTabWidget, QVBoxLayout, QWidget,
)

from ..settings import save_settings, settings_snapshot
from .paper import button, label


class Preferences(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.setObjectName("page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(12)
        layout.addWidget(label("学习配置", "pageTitle"))
        layout.addWidget(label("收到同学发来的配置文件？连接同一个 Wi-Fi，再点击下方按钮。录播音频、课件图片和课堂文字会发送给配置中指定的服务。", "muted"))
        layout.addWidget(button("导入学习配置…", self.choose_profile, "folder-open", "primary"))
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.fields = {}
        titles = {"asr": "语音转写", "vision": "课件识别", "digest": "笔记与总结"}
        for section, title in titles.items():
            tab = QWidget()
            content = QVBoxLayout(tab)
            content.setContentsMargins(18, 18, 18, 14)
            hints = {"asr": "按服务商支持的音频协议填写完整地址。", "vision": "使用支持图像输入的对话模型，逐帧识别课件文字。",
                     "digest": "课堂笔记、作业要求和复习总览共用这套文本模型。"}
            content.addWidget(label(hints[section], "muted"))
            form = QFormLayout()
            form.setVerticalSpacing(10)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            values = self.fields[section] = {}
            if section == "asr":
                protocol = QComboBox()
                protocol.addItem("音频对话 · chat/completions", "chat")
                protocol.addItem("标准转写 · audio/transcriptions", "transcriptions")
                values["protocol"] = protocol
                form.addRow("接口协议", protocol)
            for key, name in (("url", "API 地址"), ("model", "模型名称"), ("fallback_models", "备用模型"), ("key", "API 密钥")):
                field = values[key] = QLineEdit()
                field.setObjectName(section + "_" + key)
                if key == "url":
                    field.setPlaceholderText("https://…/v1/chat/completions" if section == "asr" else "可单独填写服务地址")
                elif key == "fallback_models":
                    field.setPlaceholderText("可选，用英文逗号分隔")
                elif key == "key":
                    field.setEchoMode(QLineEdit.EchoMode.Password)
                form.addRow(name, field)
            clear = values["clear_key"] = QCheckBox("清除本项已保存的密钥")
            form.addRow("", clear)
            content.addLayout(form)
            content.addStretch()
            self.tabs.addTab(tab, title)
        watch = QFrame()
        watch.setObjectName("card")
        watch_form = QFormLayout(watch)
        self.watch = QLineEdit()
        self.watch.setPlaceholderText("用逗号分隔，留空表示全部课程")
        watch_form.addRow("每日任务的课程", self.watch)
        layout.addWidget(watch)
        layout.addWidget(label("学习配置可能含有访问密钥，只交给获准使用的同学。自己的校园登录状态保存在本机。", "small"))
        actions = QHBoxLayout()
        actions.addWidget(button("保存设置", self.save, role="primary"))
        self.feedback = label("", "status")
        actions.addWidget(self.feedback, 1)
        layout.addLayout(actions)
        layout.addStretch()
        self.populate(settings_snapshot(cfg))

    def populate(self, snapshot):
        for section, fields in self.fields.items():
            data = snapshot[section]
            fields["url"].setText(data["url"])
            fields["model"].setText(data["model"])
            fields["fallback_models"].setText(", ".join(data["fallback_models"]))
            fields["key"].clear()
            fields["key"].setPlaceholderText("已保存 · 留空保留" if data["has_key"] else "可选，按服务要求填写")
            fields["clear_key"].setChecked(False)
            if "protocol" in fields:
                fields["protocol"].setCurrentIndex(fields["protocol"].findData(data["protocol"]))
        self.watch.setText(", ".join(snapshot["watch"]["courses"]))

    def save(self):
        def split(text):
            return [s.strip() for s in text.replace("，", ",").split(",") if s.strip()]
        payload = {"watch": {"courses": split(self.watch.text())}}
        for section, fields in self.fields.items():
            entry = {k: fields[k].text().strip() for k in ("url", "model")}
            entry["fallback_models"] = split(fields["fallback_models"].text())
            entry["clear_key"] = fields["clear_key"].isChecked()
            if fields["key"].text():
                entry["key"] = fields["key"].text()
            if "protocol" in fields:
                entry["protocol"] = fields["protocol"].currentData()
            payload[section] = entry
        try:
            self.populate(save_settings(self.cfg, payload))
        except (OSError, ValueError, RuntimeError) as exc:
            self.feedback.setText(str(exc))
            return
        self.feedback.setText("已保存，下一个任务将使用这些设置。")

    def choose_profile(self):
        from ..settings import import_profile
        filename, _ = QFileDialog.getOpenFileName(self, "选择学习配置", "", "学习配置 (*.json)")
        if not filename:
            return
        try:
            self.populate(import_profile(self.cfg, filename))
        except (ValueError, RuntimeError, OSError) as exc:
            self.feedback.setText(f"导入失败。{exc}")
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self.feedback.setText("导入成功。返回工作台，登录校园平台后即可选择课程。")
        QMessageBox.information(self, "配置已导入", self.feedback.text())
