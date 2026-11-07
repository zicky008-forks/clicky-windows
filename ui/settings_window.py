"""Clicky's native settings, history and first-run feature guide."""
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QTabWidget, QLabel, QCheckBox, QComboBox, QLineEdit, QSpinBox, QPushButton, QPlainTextEdit, QMessageBox, QProgressBar
from config import cfg
from storage import setting, set_setting, data_dir

MIC_TEST = "Thử micrô (3 giây)"
MIC_GONE = "Micrô chị chọn trước đây không còn trên máy, em đang dùng micrô mặc định của Windows."
DOWNLOAD = "Tải bản cài mới"


# Voice and recognition choices. Plain ones are for everyone; the rest show only in technical mode
# (tech_mode.py): a beta user never meets an engine name, an online provider or a key.
TTS_PLAIN = (("Tự động", 'auto'), ("Tiếng Việt trên máy, không cần mạng", 'vi_offline'))
TTS_TECH = (("Windows voice — offline (English only)", 'sapi'),
            ("Microsoft online — sends each spoken sentence to Microsoft", 'edge_tts'),
            ("OpenAI — API key", 'openai'), ("ElevenLabs — API key", 'elevenlabs'))
STT_PLAIN = (("Tự động", ''), ("Tiếng Việt trên máy, không cần mạng", 'sherpa_vi'), ("Gõ phím thay vì nói", 'typed'))
STT_TECH = (("Local Whisper", 'faster_whisper'), ("Local whisper.cpp", 'whisper_cpp'),
            ("Deepgram — internet", 'deepgram'), ("OpenAI Whisper — internet", 'openai'))

GUIDE = ("1. Thả file hoặc cả thư mục vào ô thả của Clicky, hoặc gõ: làm báo cáo tháng từ thư mục này.\n\n"
         "2. Em đọc file rồi hiện phiếu nhận file. Chị xem lại, đúng thì bấm \"Đúng rồi, làm tiếp\".\n\n"
         "3. Em không sửa file gốc của chị. Em giữ một bản gốc và ghi mọi thay đổi vào sheet \"Bien ban Clicky\".\n\n"
         "4. Phím Esc luôn dừng Clicky.\n\n"
         "5. Muốn không gửi gì ra khỏi máy, chị bật \"Chỉ chạy trên máy\" ở thẻ Chung.")
GUIDE_TECH = ("Tutor features: hold the talk shortcut or type; ask 'where is the Save button?' for pointing; ask for a "
              "graph or 3D shape to open Clicky Sketch; say 'click the Save button for me' for Clicky Drive; use Work & "
              "routines for background answers. Offline use: choose Ollama/LM Studio, local Whisper, Windows voice, "
              "and leave web search off.")
MIC_PROBLEMS = (("No microphone audio", "Micrô chưa gửi tiếng nào tới. Chị kiểm tra micrô đã chọn và quyền dùng micrô "
                                        "trong Cài đặt của Windows."),
                ("The microphone produced silence", "Micrô chỉ thu được im lặng. Chị kiểm tra nút tắt tiếng và mức "
                                                    "thu âm của Windows."))
TECH_SWITCH = "Chế độ kỹ thuật: hiện các mục dành cho người kiểm thử và lập trình"


class SettingsWindow(QWidget):
    update_ready = pyqtSignal(object)
    installer_ready = pyqtSignal(str)
    shortcuts_changed = pyqtSignal()
    open_keys = pyqtSignal()
    preview_voice = pyqtSignal()
    show_sketch = pyqtSignal()
    show_work = pyqtSignal()
    open_connections = pyqtSignal()
    open_outgoing_log = pyqtSignal()
    microphone_level = pyqtSignal(float)
    tech_mode_changed = pyqtSignal(bool)

    def __init__(self, manager):
        super().__init__()
        import tech_mode
        self.manager = manager
        self.setWindowTitle("Cài đặt Clicky")
        self.resize(620, 600)
        self._tech_rows = []          # (form, widget) rows shown only in technical mode
        self._tech_widgets = []
        layout = QVBoxLayout(self)
        tabs = self.tabs = QTabWidget()
        layout.addWidget(tabs)
        behavior = QWidget()
        form = QFormLayout(behavior)
        from office.outgoing import LOCAL_ONLY_LABEL, local_only, local_only_forced
        self.local_only = QCheckBox(LOCAL_ONLY_LABEL)
        self.local_only.setChecked(local_only())
        if local_only_forced():
            self.local_only.setEnabled(False)             # CLICKY_LOCAL_ONLY=1 keeps it on
        self.local_only.toggled.connect(lambda value: self.apply(lambda: manager.set_local_only(value)))
        form.addRow(self.local_only)
        outgoing_log = QPushButton("Nhật ký gửi đi…")
        outgoing_log.clicked.connect(self.open_outgoing_log.emit)
        form.addRow(outgoing_log)
        for label,key,default,callback,tech in (
            ("Che các cửa sổ nhạy cảm khi Clicky nhìn màn hình",'privacy_guard',True,manager.set_privacy_guard,False),
            ("Lưu lịch sử trò chuyện trên máy này",'journal',True,manager.set_journal,False),
            ("Cho phép tìm trên mạng",'web_search',False,manager.set_web_search,True),
            ("Trả lời theo ngôn ngữ của người hỏi",'multilang',True,manager.set_multilang,True),
            ("Đọc chữ trong ảnh màn hình (OCR)",'ocr',True,manager.set_ocr_enabled,True),
            ("Dùng ngữ cảnh mã nguồn trong trình soạn code",'code_mode',True,manager.set_code_mode_auto,True),
        ):
            box = QCheckBox(label)
            box.setChecked(setting(key,default))
            box.toggled.connect(lambda value, call=callback:self.apply(lambda:call(value)))
            form.addRow(box)
            if tech:
                self._tech_rows.append((form, box))
        privacy_note = QLabel("Clicky che các cửa sổ có tiêu đề nhạy cảm trên mọi màn hình, nhưng không nhận ra được "
                              "mọi thông tin bí mật nằm trong một tài liệu thường.")
        privacy_note.setWordWrap(True)
        form.addRow(privacy_note)
        keys = QPushButton("Manage protected API keys…")
        keys.clicked.connect(self.open_keys.emit)
        form.addRow(keys)
        connections = QPushButton("Browser and app connections…")
        connections.clicked.connect(self.open_connections.emit)
        form.addRow(connections)
        self._tech_rows += [(form, keys), (form, connections)]
        tabs.addTab(behavior,"Chung")

        voice_page = QWidget()
        voice = QFormLayout(voice_page)
        self.provider = QComboBox()
        voice.addRow("Giọng đọc",self.provider)
        self.transcription = QComboBox()
        voice.addRow("Nghe giọng nói",self.transcription)
        self._fill_voice_choices()
        self.voice_name = QLineEdit(setting('voice' if cfg.tts_provider()=='sapi' else 'voice_'+cfg.tts_provider(),''))
        self.voice_name.setPlaceholderText("Installed Windows voice, Edge name, or provider voice ID")
        voice.addRow("Voice name (blank uses default)",self.voice_name)
        self._tech_rows.append((voice, self.voice_name))
        self.rate = QSpinBox()
        self.rate.setRange(-10,10)
        self.rate.setValue(setting('speech_rate',0))
        voice.addRow("Tốc độ đọc",self.rate)
        self.captions = QCheckBox("Hiện chữ khi Clicky nói")
        self.captions.setChecked(setting('captions',True))
        voice.addRow(self.captions)
        save_voice = QPushButton("Áp dụng")
        save_voice.clicked.connect(self.save_voice)
        voice.addRow(save_voice)
        preview = QPushButton("Nghe thử")
        preview.clicked.connect(self.preview_voice.emit)
        voice.addRow(preview)
        tabs.addTab(voice_page,"Giọng nói")

        microphone_page = QWidget()
        mic = QFormLayout(microphone_page)
        self.microphone = QComboBox()
        mic.addRow('Micrô', self.microphone)
        refresh_mics = QPushButton('Tìm lại micrô')
        refresh_mics.clicked.connect(self.refresh_microphones)
        mic.addRow(refresh_mics)
        apply_mic = QPushButton('Dùng micrô này')
        apply_mic.clicked.connect(lambda: manager.set_mic_device(self.microphone.currentData()))
        mic.addRow(apply_mic)
        self.mic_level = QProgressBar()
        self.mic_level.setRange(0, 100)
        self.mic_level.setTextVisible(False)
        mic.addRow('Mức tiếng', self.mic_level)
        self.mic_status = QLabel('')
        self.mic_status.setWordWrap(True)
        mic.addRow(self.mic_status)
        self.mic_test = QPushButton(MIC_TEST)
        self.mic_test.clicked.connect(self.test_microphone)
        mic.addRow(self.mic_test)
        note = QLabel('Lần thử này chỉ diễn ra trên máy, không lưu lại và không gửi đi đâu. Chị nói trong lúc thanh mức '
                      'tiếng đang chạy. Nếu không có tiếng, chị kiểm tra nút tắt tiếng và quyền dùng micrô của Windows.')
        note.setWordWrap(True)
        mic.addRow(note)
        self._mic_listener = None
        self._mic_timer = QTimer(self)
        self._mic_timer.setSingleShot(True)
        self._mic_timer.timeout.connect(self.finish_microphone_test)
        self.microphone_level.connect(lambda level: self.mic_level.setValue(min(100, int(level * 1000))))
        self.refresh_microphones()
        tabs.addTab(microphone_page, 'Micrô')

        shortcuts_page = QWidget()
        shortcuts = QFormLayout(shortcuts_page)
        self.talk_key = QLineEdit(cfg.hotkey)
        self.text_key = QLineEdit(setting('text_hotkey','ctrl+alt+t'))
        self.dictation_key = QLineEdit(setting('dictation_hotkey','ctrl+alt+d'))
        shortcuts.addRow("Giữ để nói",self.talk_key)
        shortcuts.addRow("Mở khung gõ",self.text_key)
        shortcuts.addRow("Giữ để đọc cho Clicky gõ",self.dictation_key)
        shortcuts.addRow(QLabel("Ví dụ: ctrl+win, ctrl+alt+space. Phím Esc luôn dừng Clicky."))
        save_shortcuts = QPushButton("Áp dụng phím tắt")
        save_shortcuts.clicked.connect(self.save_shortcuts)
        shortcuts.addRow(save_shortcuts)
        tabs.addTab(shortcuts_page,"Phím tắt")

        history = QWidget()
        history_layout = QVBoxLayout(history)
        history_layout.addWidget(QLabel("Lịch sử trò chuyện chỉ lưu trên máy này, trong tài khoản Windows của chị."))
        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        history_layout.addWidget(self.history)
        refresh = QPushButton("Làm mới")
        refresh.clicked.connect(self.refresh_history)
        history_layout.addWidget(refresh)
        clear = QPushButton("Xoá lịch sử trò chuyện")
        clear.clicked.connect(self.clear_history)
        history_layout.addWidget(clear)
        tabs.addTab(history,"Lịch sử")

        guide = QWidget()
        guide_layout = QVBoxLayout(guide)
        help_text = QLabel(GUIDE)
        help_text.setWordWrap(True)
        guide_layout.addWidget(help_text)
        tech_help = QLabel(GUIDE_TECH)
        tech_help.setWordWrap(True)
        guide_layout.addWidget(tech_help)
        self._tech_widgets.append(tech_help)
        for text,signal in (("Try Clicky Sketch",self.show_sketch),("Open Work & routines",self.show_work)):
            button = QPushButton(text)
            button.clicked.connect(signal.emit)
            guide_layout.addWidget(button)
            self._tech_widgets.append(button)
        guide_layout.addStretch()
        tabs.addTab(guide,"Bắt đầu")
        updates = QWidget()
        update_layout = QVBoxLayout(updates)
        from updates import VERSION
        self.update_status = QLabel(f"Bản đang dùng: {VERSION}. Clicky tự cập nhật lúc rảnh và báo một dòng khi mở lần sau.")
        self.update_status.setWordWrap(True)
        update_layout.addWidget(self.update_status)
        check = QPushButton('Kiểm tra bản mới')
        check.clicked.connect(self.check_updates)
        update_layout.addWidget(check)
        self.install = QPushButton(DOWNLOAD)
        self.install.setEnabled(False)
        self.install.clicked.connect(self.download_update)
        update_layout.addWidget(self.install)
        update_layout.addStretch()
        tabs.addTab(updates,'Cập nhật')

        # Technical mode: reachable only by holding Shift while opening Cài đặt (or CLICKY_TECH_MODE=1).
        tech_page = QWidget()
        tech_layout = QVBoxLayout(tech_page)
        self.tech_switch = QCheckBox(TECH_SWITCH)
        self.tech_switch.setChecked(tech_mode.on())
        self.tech_switch.setEnabled(not tech_mode.forced())
        self.tech_switch.toggled.connect(self._set_tech_mode)
        tech_layout.addWidget(self.tech_switch)
        tech_layout.addStretch()
        self._tech_tab = tabs.addTab(tech_page, "Kỹ thuật")
        self._update=None
        self.update_ready.connect(self.show_update)
        self.installer_ready.connect(self.install_update)
        self.refresh_history()
        self.apply_tech_mode()

    def present(self, shift: bool = False):
        """Open the window. Holding Shift while opening shows the "Kỹ thuật" tab."""
        import tech_mode
        self.tabs.setTabVisible(self._tech_tab, bool(shift) or tech_mode.on())
        self.show()
        self.raise_()
        self.activateWindow()

    def _set_tech_mode(self, value):
        import tech_mode
        self.apply(lambda: tech_mode.set_on(value))
        self.apply_tech_mode()
        self.tech_mode_changed.emit(tech_mode.on())

    def apply_tech_mode(self):
        import tech_mode
        shown = tech_mode.on()
        for form, widget in self._tech_rows:
            form.setRowVisible(widget, shown)
        for widget in self._tech_widgets:
            widget.setVisible(shown)
        if not shown and not self.isVisible():
            self.tabs.setTabVisible(self._tech_tab, False)
        self._fill_voice_choices()

    def _fill_voice_choices(self):
        """Plain choices for everyone, engine choices in technical mode. A choice already saved stays listed."""
        import os
        import tech_mode
        tech = tech_mode.on()
        for combo, plain, more, current in (
                (self.provider, TTS_PLAIN, TTS_TECH, setting('tts_provider','auto')),
                (self.transcription, STT_PLAIN, STT_TECH, os.getenv('CLICKY_STT',''))):
            combo.blockSignals(True)
            combo.clear()
            for label, value in plain + more:
                if tech or (label, value) in plain or value == current:
                    combo.addItem(label if tech or (label, value) in plain else "Lựa chọn đang dùng", value)
            combo.setCurrentIndex(max(0, combo.findData(current)))
            combo.blockSignals(False)

    def closeEvent(self, event):
        import tech_mode
        self.finish_microphone_test()
        if not tech_mode.on():
            self.tabs.setTabVisible(self._tech_tab, False)
        super().closeEvent(event)

    def check_updates(self):
        async def check():
            try:
                from updates import latest
                self.update_ready.emit(await latest())
            except Exception as exc:self.update_ready.emit({'error':str(exc)})
        self.manager._submit(check())

    def refresh_microphones(self):
        from audio.devices import input_devices, recommended_inputs, resolve_input
        self.microphone.clear()
        self.microphone.addItem('Mặc định của Windows (nên dùng)', -1)
        try:
            devices = input_devices()
            preferred = recommended_inputs(devices)
            for item in preferred + [item for item in devices if item not in preferred]:
                prefix = '' if item in preferred else 'Driver khác: '
                self.microphone.addItem(prefix + item['name'] + ' (' + item['host_name'] + ')', item['index'])
            saved = setting('mic_identity', {'default': True})
            selected, warning = resolve_input(cfg.mic_device_index, saved)
            if saved != {'default': True} and (saved or cfg.mic_device_index is not None):
                self.microphone.setCurrentIndex(max(0, self.microphone.findData(selected['index'])))
            self.mic_status.setText(MIC_GONE if warning else 'Sẵn sàng. Chị thử micrô đã chọn trước khi dùng nhé.')
        except Exception as exc:
            self.mic_status.setText(_plain('Chưa dùng được micrô.', exc))

    def test_microphone(self):
        if self._mic_listener:
            self.finish_microphone_test()
            return
        from audio.ambient_listener import AmbientListener
        from audio.devices import input_devices, identity
        try:
            selected = self.microphone.currentData()
            item = next((item for item in input_devices() if item['index'] == selected), None)
            if selected is not None and selected >= 0 and not item:
                raise ValueError('Micrô này không còn cắm vào máy. Chị bấm Tìm lại micrô.')
            saved = identity(item) if item else {'default': True}
            self._mic_listener = AmbientListener(self.microphone_level.emit, lambda: None,
                                                 device=selected, ambient=False, identity=saved)
            self._mic_listener.start_recording()
            self.mic_status.setText('Chị nói đi, em đang thử ' + self._mic_listener.device_name)
            self.mic_test.setText('Dừng thử')
            self._mic_timer.start(3000)
        except Exception as exc:
            if self._mic_listener:
                self._mic_listener.stop()
                self._mic_listener = None
            self.mic_status.setText(str(exc))

    def finish_microphone_test(self):
        self._mic_timer.stop()
        listener, self._mic_listener = self._mic_listener, None
        if listener:
            from audio.capture import capture_problem
            try:
                pcm = listener.stop_recording()
                problem = capture_problem(pcm)
                self.mic_status.setText(_mic_problem(problem) or 'Đã nghe được tiếng từ ' + listener.device_name
                                        + '. Chị bấm “Dùng micrô này” để chọn micrô này.')
            finally:
                listener.stop()
        self.mic_test.setText(MIC_TEST)
        self.mic_level.setValue(0)

    def show_update(self,value):
        import tech_mode
        self._update=value
        self.install.setEnabled(bool(value and (value.get('asset') or value.get('velopack'))))
        self.install.setText('Cập nhật và mở lại' if value and value.get('velopack') else DOWNLOAD)
        if value and value.get('error'):
            text = _plain('Em chưa kiểm tra được bản mới. Chị thử lại sau nhé.', value['error'])
        elif value:
            text = 'Có bản mới: ' + value['version'] + ('\n' + value['url'] if tech_mode.on() else '')
        else:
            text = 'Chị đang dùng bản mới nhất.'
        self.update_status.setText(text)

    def download_update(self):
        self.install.setEnabled(False)
        if self._update and self._update.get('velopack'):
            if QMessageBox.question(self,'Cập nhật Clicky','Em tải bản mới rồi mở lại Clicky nhé?',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:
                self.install.setEnabled(True)
                return
            self.update_status.setText('Em đang tải bản mới…')
            async def apply():
                try:
                    from updates import apply_in_place
                    await apply_in_place(self._update)
                except Exception as exc:self.update_ready.emit({'error':str(exc)})
            self.manager._submit(apply())
            return
        async def fetch():
            try:
                from updates import download
                self.installer_ready.emit(await download(self._update['asset']))
            except Exception as exc:self.update_ready.emit({'error':str(exc)})
        self.manager._submit(fetch())

    def install_update(self,path):
        if QMessageBox.question(self,'Bản cài đã kiểm tra','Em đóng Clicky và mở bản cài mới nhé?',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes:
            import subprocess
            subprocess.Popen([path])
            from PyQt6.QtWidgets import QApplication
            QApplication.instance().quit()

    def apply(self,callback):
        try:
            callback()
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,"Chưa lưu được cài đặt",str(exc))

    def save_voice(self):
        def save():
            self.manager.stop()
            provider = self.provider.currentData()
            if provider == 'openai' and not cfg.openai_api_key or provider == 'elevenlabs' and not cfg.elevenlabs_api_key:
                raise ValueError("Add an API key for that voice provider first")
            import os
            stt = self.transcription.currentData()
            if stt=='deepgram' and not cfg.deepgram_api_key or stt=='openai' and not cfg.openai_api_key:
                raise ValueError('Add the API key for that recognition provider first')
            set_setting('tts_provider',provider)
            cfg._write_env('CLICKY_STT',stt)
            os.environ['CLICKY_STT'] = stt
            self.manager._stt = None
            set_setting('speech_rate',self.rate.value())
            set_setting('captions',self.captions.isChecked())
            selected = cfg.tts_provider()
            set_setting('voice' if selected == 'sapi' else 'voice_'+selected,self.voice_name.text().strip())
            self.manager._tts = None
        self.apply(save)

    def save_shortcuts(self):
        def save():
            import keyboard
            values = [field.text().strip().lower() for field in (self.talk_key,self.text_key,self.dictation_key)]
            if len(set(values)) != 3 or any(not value or value == 'esc' for value in values):
                raise ValueError("Chị chọn ba phím tắt khác nhau, không để trống. Phím Esc đã dành để dừng Clicky.")
            for value in values:
                keyboard.parse_hotkey(value)
            cfg._write_env('CLICKY_HOTKEY',values[0])
            cfg.hotkey = values[0]
            set_setting('text_hotkey',values[1])
            set_setting('dictation_hotkey',values[2])
            self.shortcuts_changed.emit()
        self.apply(save)

    def refresh_history(self):
        import session_store
        self.history.setPlainText('\n\n'.join('['+app+']\n'+'\n\n'.join(row['role']+': '+row['content'] for row in rows) for app,rows in session_store.recent()))

    def clear_history(self):
        if QMessageBox.question(self,"Xoá lịch sử?","Xoá lịch sử trò chuyện đã lưu trên máy này?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.manager.clear_history()
            self.refresh_history()


def _plain(sentence: str, exc) -> str:
    """A plain sentence for everyone; the technical detail only in technical mode."""
    import tech_mode
    return sentence + (f"\n{exc}" if tech_mode.on() else "")


def _mic_problem(problem: str) -> str:
    for start, text in MIC_PROBLEMS:
        if problem.startswith(start):
            return text
    return problem
