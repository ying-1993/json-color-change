import sys, os, json, copy, zipfile
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QListWidget, QListWidgetItem, QFileDialog,
    QComboBox, QSlider, QColorDialog, QMessageBox,
    QSplitter, QGroupBox, QInputDialog
)
from PySide6.QtCore import Qt, QTimer, Signal, QUrl
from PySide6.QtGui import QColor, QPainter, QPen

from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings

from PIL import ImageGrab
import screeninfo


def normalize_hex(hex_str):
    if not hex_str:
        return None
    h = hex_str.strip().lower().lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    if len(h) != 6 or any(c not in '0123456789abcdef' for c in h):
        return None
    return '#' + h


def rgba_to_hex(r, g, b):
    return '#{:02x}{:02x}{:02x}'.format(
        max(0, min(255, int(round(r * 255)))),
        max(0, min(255, int(round(g * 255)))),
        max(0, min(255, int(round(b * 255))))
    )


def hex_to_rgba(hex_str, alpha=1.0):
    h = normalize_hex(hex_str)
    if not h:
        return None
    return [int(h[1:3],16)/255.0, int(h[3:5],16)/255.0, int(h[5:7],16)/255.0, alpha]


def extract_colors_from_lottie(obj, color_set):
    if isinstance(obj, dict):
        if 'k' in obj and isinstance(obj['k'], list) and len(obj['k']) == 4 and isinstance(obj['k'][3], (int, float)):
            try:
                color_set.add(rgba_to_hex(obj['k'][0], obj['k'][1], obj['k'][2]))
            except Exception:
                pass
        for v in obj.values():
            extract_colors_from_lottie(v, color_set)
    elif isinstance(obj, list):
        for item in obj:
            extract_colors_from_lottie(item, color_set)


def replace_colors_in_lottie(obj, color_map):
    if isinstance(obj, dict):
        if 'k' in obj and isinstance(obj['k'], list) and len(obj['k']) == 4 and isinstance(obj['k'][3], (int, float)):
            try:
                old_hex = rgba_to_hex(obj['k'][0], obj['k'][1], obj['k'][2])
                old_norm = normalize_hex(old_hex)
                if old_norm and old_norm in color_map:
                    new_rgba = hex_to_rgba(color_map[old_norm], obj['k'][3])
                    if new_rgba:
                        obj['k'] = new_rgba
            except Exception:
                pass
        for v in obj.values():
            replace_colors_in_lottie(v, color_map)
    elif isinstance(obj, list):
        for item in obj:
            replace_colors_in_lottie(item, color_map)


class ScreenColorPicker(QWidget):
    color_picked = Signal(QColor)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.showFullScreen()

    def mousePressEvent(self, event):
        pos = event.globalPosition().toPoint()
        try:
            img = ImageGrab.grab(bbox=(pos.x(), pos.y(), pos.x()+1, pos.y()+1))
            color = img.getpixel((0,0))
            self.color_picked.emit(QColor(*color[:3]))
        except Exception as e:
            print('吸色失败:', e)
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0,0,0,80))
        painter.setPen(QPen(QColor(255,255,255,200), 2))
        painter.drawText(self.rect(), Qt.AlignCenter, "点击屏幕任意位置取色，按 ESC 取消")


class LottieColorTool(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Lottie 颜色批量修改工具')
        self.resize(1400, 900)
        self.animations = []
        self.current_index = -1
        self.color_map = {}
        self.history = []
        self.total_frames = 0
        self.current_frame = 0
        self.is_playing = False
        self.play_timer = QTimer()
        self.play_timer.timeout.connect(self._on_play_tick)
        self._build_ui()
        self._refresh_color_list()
        self._refresh_history()

    def _build_ui(self):
        wrapper = QWidget()
        vbox = QVBoxLayout(wrapper)
        vbox.setContentsMargins(6,6,6,6)

        top_bar = QHBoxLayout()
        self.btn_upload = QPushButton('📁 上传 JSON')
        self.btn_upload.clicked.connect(self._upload_files)
        self.btn_clear = QPushButton('🗑️ 清空全部')
        self.btn_clear.clicked.connect(self._clear_all)
        top_bar.addWidget(self.btn_upload)
        top_bar.addWidget(self.btn_clear)
        top_bar.addStretch()
        top_bar.addWidget(QLabel('🎨 Lottie 颜色批量修改'))
        vbox.addLayout(top_bar)

        main_layout = QHBoxLayout()
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0,0,0,0)
        left_header = QHBoxLayout()
        left_header.addWidget(QLabel('提取的颜色'))
        self.lbl_color_count = QLabel('0')
        left_header.addWidget(self.lbl_color_count)
        left_header.addStretch()
        left_layout.addLayout(left_header)
        self.color_list_widget = QListWidget()
        left_layout.addWidget(self.color_list_widget)
        left_panel.setFixedWidth(320)

        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(0,0,0,0)
        ctrl_bar = QHBoxLayout()
        self.btn_play = QPushButton('▶️ 播放')
        self.btn_play.setEnabled(False)
        self.btn_play.clicked.connect(self._toggle_play)
        self.cmb_anim = QComboBox()
        self.cmb_anim.setMinimumWidth(160)
        self.cmb_anim.currentIndexChanged.connect(self._on_anim_changed)
        self.slider_frame = QSlider(Qt.Horizontal)
        self.slider_frame.setEnabled(False)
        self.slider_frame.valueChanged.connect(self._on_frame_slider)
        self.lbl_frame = QLabel('0/0')
        self.btn_prev = QPushButton('◀')
        self.btn_prev.setEnabled(False)
        self.btn_prev.clicked.connect(lambda: self._step_frame(-1))
        self.btn_next = QPushButton('▶')
        self.btn_next.setEnabled(False)
        self.btn_next.clicked.connect(lambda: self._step_frame(1))
        ctrl_bar.addWidget(self.btn_play)
        ctrl_bar.addWidget(QLabel('动画:'))
        ctrl_bar.addWidget(self.cmb_anim)
        ctrl_bar.addWidget(QLabel('帧:'))
        ctrl_bar.addWidget(self.slider_frame, 1)
        ctrl_bar.addWidget(self.lbl_frame)
        ctrl_bar.addWidget(self.btn_prev)
        ctrl_bar.addWidget(self.btn_next)
        center_layout.addLayout(ctrl_bar)

        preview_split = QSplitter(Qt.Horizontal)
        self.before_view = QWebEngineView()
        self.after_view = QWebEngineView()
        for v in (self.before_view, self.after_view):
            v.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
            v.settings().setAttribute(QWebEngineSettings.JavascriptEnabled, True)
            v.setMinimumSize(300, 300)
        preview_split.addWidget(self._make_preview_box('修改前', self.before_view))
        preview_split.addWidget(self._make_preview_box('修改后', self.after_view))
        center_layout.addWidget(preview_split, 1)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0,0,0,0)
        right_layout.addWidget(QLabel('历史版本'))
        self.history_list = QListWidget()
        self.history_list.itemClicked.connect(self._on_history_clicked)
        right_layout.addWidget(self.history_list, 1)
        export_box = QGroupBox('导出')
        export_layout = QVBoxLayout(export_box)
        self.btn_export_current = QPushButton('📥 导出当前动画 (JSON)')
        self.btn_export_current.clicked.connect(self._export_current)
        self.btn_export_current.setEnabled(False)
        self.btn_export_all = QPushButton('📦 批量导出所有 (ZIP)')
        self.btn_export_all.clicked.connect(self._export_all)
        self.btn_export_all.setEnabled(False)
        export_layout.addWidget(self.btn_export_current)
        export_layout.addWidget(self.btn_export_all)
        right_layout.addWidget(export_box)
        right_panel.setFixedWidth(260)

        main_layout.addWidget(left_panel)
        main_layout.addWidget(center_panel, 1)
        main_layout.addWidget(right_panel)
        vbox.addLayout(main_layout, 1)
        self.setCentralWidget(wrapper)

        self.setStyleSheet("""
            QMainWindow{background:#1e1e2f;color:#e0e0e0;}
            QWidget{color:#e0e0e0;}
            QPushButton{background:#3e3e55;border:none;padding:6px 12px;border-radius:4px;}
            QPushButton:hover{background:#4f4f6a;}
            QPushButton:disabled{background:#2a2a3a;color:#666;}
            QComboBox{background:#2f2f45;border:1px solid #4a4a6a;padding:4px;border-radius:4px;}
            QLabel{color:#ddd;}
            QGroupBox{border:1px solid #3e3e55;border-radius:6px;margin-top:10px;}
            QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px;color:#aaa;}
        """)

    def _make_preview_box(self, title, view):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0,0,0,0)
        label = QLabel(title)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet('background:#222235;padding:6px;color:#aaa;border-bottom:1px solid #3e3e55;')
        lay.addWidget(label)
        lay.addWidget(view, 1)
        return box

    def _upload_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, '选择 Lottie JSON 文件', '', 'JSON Files (*.json)')
        if not files:
            return
        for path in files:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if not (isinstance(data, dict) and 'v' in data and 'layers' in data):
                    QMessageBox.warning(self, '跳过', f'{os.path.basename(path)} 不是有效的 Lottie JSON')
                    continue
                name = os.path.splitext(os.path.basename(path))[0]
                self.animations.append({'name': name, 'raw': data, 'modified': copy.deepcopy(data)})
            except Exception as e:
                QMessageBox.warning(self, '解析失败', f'{os.path.basename(path)}: {e}')
        if self.animations:
            self._refresh_anim_combo()
            if self.current_index < 0:
                self.current_index = 0
                self.cmb_anim.setCurrentIndex(0)
                self._load_animation_to_preview(0)
            self._refresh_color_list()
            self._rebuild_modified()

    def _refresh_color_list(self):
        all_colors = set()
        for anim in self.animations:
            extract_colors_from_lottie(anim['raw'], all_colors)
        self.lbl_color_count.setText(str(len(all_colors)))
        self.color_list_widget.clear()
        for hex_color in sorted(all_colors):
            replaced = self.color_map.get(hex_color)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, hex_color)
            widget = self._make_color_item_widget(hex_color, replaced)
            item.setSizeHint(widget.sizeHint())
            self.color_list_widget.addItem(item)
            self.color_list_widget.setItemWidget(item, widget)

    def _make_color_item_widget(self, hex_color, replaced_hex):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4,4,4,4)
        lay.setSpacing(8)
        swatch = QLabel()
        swatch.setFixedSize(32,32)
        swatch.setStyleSheet(f'background:{hex_color};border:1px solid #555;border-radius:4px;')
        info = QLabel(f'{hex_color} → {replaced_hex}' if replaced_hex else hex_color)
        info.setStyleSheet('color:#6fcf97;font-family:monospace;' if replaced_hex else 'color:#ccc;font-family:monospace;')
        btn_pick = QPushButton('🎯')
        btn_pick.setFixedWidth(28)
        btn_pick.clicked.connect(lambda: self._start_eye_dropper(hex_color))
        btn_edit = QPushButton('✏️')
        btn_edit.setFixedWidth(28)
        btn_edit.clicked.connect(lambda: self._manual_edit_color(hex_color))
        lay.addWidget(swatch)
        lay.addWidget(info, 1)
        lay.addWidget(btn_pick)
        lay.addWidget(btn_edit)
        if replaced_hex:
            btn_reset = QPushButton('↺')
            btn_reset.setFixedWidth(28)
            btn_reset.clicked.connect(lambda: self._reset_color(hex_color))
            lay.addWidget(btn_reset)
        return w

    def _start_eye_dropper(self, original_hex):
        picker = ScreenColorPicker()
        picker.color_picked.connect(lambda c: self._on_color_picked(original_hex, c))
        picker.show()

    def _on_color_picked(self, original_hex, color):
        hex_str = normalize_hex(color.name())
        if hex_str:
            self._set_color_replacement(original_hex, hex_str)

    def _manual_edit_color(self, original_hex):
        current = self.color_map.get(original_hex, original_hex)
        text, ok = QInputDialog.getText(self, '输入色值', '输入替换色值（如 #ff0000）:', text=current)
        if ok and text:
            norm = normalize_hex(text)
            if norm:
                self._set_color_replacement(original_hex, norm)
            else:
                QMessageBox.warning(self, '无效色值', '请输入有效的十六进制色值')

    def _reset_color(self, original_hex):
        self._set_color_replacement(original_hex, None)

    def _set_color_replacement(self, original_hex, new_hex):
        if new_hex:
            self.color_map[original_hex] = new_hex
        else:
            self.color_map.pop(original_hex, None)
        self._rebuild_modified()
        self._refresh_color_list()
        self._add_history('修改颜色 {} → {}'.format(original_hex, new_hex or '重置'))
        if self.current_index >= 0:
            self._refresh_after_preview()

    def _rebuild_modified(self):
        for anim in self.animations:
            modified = copy.deepcopy(anim['raw'])
            replace_colors_in_lottie(modified, self.color_map)
            anim['modified'] = modified
        self._refresh_after_preview()

    def _refresh_after_preview(self):
        if self.current_index < 0 or self.current_index >= len(self.animations):
            return
        anim = self.animations[self.current_index]
        self._render_lottie(self.after_view, anim['modified'])
        self._seek_frame(self.current_frame)

    def _refresh_anim_combo(self):
        self.cmb_anim.blockSignals(True)
        self.cmb_anim.clear()
        for anim in self.animations:
            self.cmb_anim.addItem(anim['name'])
        self.cmb_anim.blockSignals(False)

    def _on_anim_changed(self, idx):
        if idx < 0 or idx >= len(self.animations):
            return
        self.current_index = idx
        self._load_animation_to_preview(idx)

    def _load_animation_to_preview(self, idx):
        if idx < 0 or idx >= len(self.animations):
            return
        anim = self.animations[idx]
        self._render_lottie(self.before_view, anim['raw'])
        self._render_lottie(self.after_view, anim['modified'])
        self._update_frame_controls(0, 0)
        self.btn_play.setEnabled(True)
        self.btn_prev.setEnabled(True)
        self.btn_next.setEnabled(True)
        self.btn_export_current.setEnabled(True)
        self.btn_export_all.setEnabled(len(self.animations) > 0)
        self.is_playing = False
        self.btn_play.setText('▶️ 播放')
        if self.play_timer.isActive():
            self.play_timer.stop()

    def _render_lottie(self, view, lottie_json):
        json_str = json.dumps(lottie_json)
        html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<script src="lottie.min.js"></script>
<style>html,body{margin:0;padding:0;background:#0a0a15;overflow:hidden;height:100%;}
#anim{width:100%;height:100%;}</style></head>
<body><div id="anim"></div>
<script>
var animData = %s;
var anim = lottie.loadAnimation({
    container: document.getElementById('anim'),
    renderer: 'svg', loop: false, autoplay: false, animationData: animData
});
anim.addEventListener('DOMLoaded', function() {
    window.totalFrames = anim.totalFrames;
    window.animInstance = anim;
    window.parent.postMessage({type:'lottie_loaded', totalFrames: anim.totalFrames}, '*');
});
window.goToFrame = function(f) { if(window.animInstance) window.animInstance.goToAndStop(f, true); };
</script></body></html>""" % json_str
        base_dir = os.path.dirname(os.path.abspath(__file__))
        view.setHtml(html, QUrl.fromLocalFile(base_dir + os.sep))
        try:
            view.page().runJavaScript('goToFrame(0);')
        except Exception:
            pass

    def _update_frame_controls(self, current, total):
        self.total_frames = max(1, total)
        self.current_frame = max(0, min(current, total-1))
        self.slider_frame.blockSignals(True)
        self.slider_frame.setMaximum(max(0, total-1))
        self.slider_frame.setValue(self.current_frame)
        self.slider_frame.blockSignals(False)
        self.slider_frame.setEnabled(total > 1)
        self.lbl_frame.setText(f'{self.current_frame}/{max(0, total-1)}')

    def _on_frame_slider(self, value):
        self._seek_frame(value)
        if self.is_playing:
            self._toggle_play()

    def _step_frame(self, delta):
        self._seek_frame(self.current_frame + delta)

    def _seek_frame(self, frame):
        if self.total_frames <= 0:
            return
        frame = max(0, min(frame, self.total_frames-1))
        self.current_frame = frame
        self.slider_frame.blockSignals(True)
        self.slider_frame.setValue(frame)
        self.slider_frame.blockSignals(False)
        self.lbl_frame.setText(f'{frame}/{self.total_frames-1}')
        for view in (self.before_view, self.after_view):
            try:
                view.page().runJavaScript(f'goToFrame({frame});')
            except Exception:
                pass

    def _toggle_play(self):
        if self.is_playing:
            self.play_timer.stop()
            self.is_playing = False
            self.btn_play.setText('▶️ 播放')
        else:
            if self.total_frames <= 1:
                return
            self.is_playing = True
            self.btn_play.setText('⏸️ 暂停')
            self.play_timer.start(33)

    def _on_play_tick(self):
        if not self.is_playing or self.total_frames <= 1:
            return
        self._seek_frame((self.current_frame + 1) % self.total_frames)

    def _add_history(self, desc):
        snapshot = dict(self.color_map)
        modified_snapshot = [{'name': a['name'], 'json': copy.deepcopy(a['modified'])} for a in self.animations]
        self.history.insert(0, {
            'time': datetime.now().strftime('%H:%M:%S'),
            'desc': desc,
            'color_map': snapshot,
            'modified_jsons': modified_snapshot
        })
        if len(self.history) > 30:
            self.history.pop()
        self._refresh_history()

    def _refresh_history(self):
        self.history_list.clear()
        for i, h in enumerate(self.history):
            item = QListWidgetItem(f"[{h['time']}] {h['desc']}")
            item.setData(Qt.UserRole, i)
            self.history_list.addItem(item)

    def _on_history_clicked(self, item):
        idx = item.data(Qt.UserRole)
        if idx is None or idx < 0 or idx >= len(self.history):
            return
        h = self.history[idx]
        self.color_map = dict(h['color_map'])
        self._rebuild_modified()
        self._refresh_color_list()
        if self.current_index >= 0:
            self._refresh_after_preview()
        self._add_history('恢复历史版本')

    def _export_current(self):
        if self.current_index < 0:
            return
        anim = self.animations[self.current_index]
        path, _ = QFileDialog.getSaveFileName(self, '保存修改后的 JSON', f'{anim["name"]}_modified.json', 'JSON (*.json)')
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(anim['modified'], f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, '导出成功', f'已保存到 {path}')

    def _export_all(self):
        if not self.animations:
            return
        path, _ = QFileDialog.getSaveFileName(self, '批量导出 ZIP', 'lottie_modified.zip', 'ZIP (*.zip)')
        if not path:
            return
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for anim in self.animations:
                json_str = json.dumps(anim['modified'], ensure_ascii=False, indent=2)
                zf.writestr(f'{anim["name"]}_modified.json', json_str)
        QMessageBox.information(self, '导出成功', f'已保存到 {path}')

    def _clear_all(self):
        if not self.animations:
            return
        reply = QMessageBox.question(self, '确认', '确定清空所有动画和颜色修改？', QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.animations.clear()
        self.color_map.clear()
        self.current_index = -1
        self._refresh_anim_combo()
        self._refresh_color_list()
        self.before_view.setHtml('')
        self.after_view.setHtml('')
        self._update_frame_controls(0, 0)
        self.btn_play.setEnabled(False)
        self.btn_prev.setEnabled(False)
        self.btn_next.setEnabled(False)
        self.btn_export_current.setEnabled(False)
        self.btn_export_all.setEnabled(False)
        if self.play_timer.isActive():
            self.play_timer.stop()
        self.is_playing = False
        self.btn_play.setText('▶️ 播放')


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = LottieColorTool()
    window.show()
    sys.exit(app.exec())
