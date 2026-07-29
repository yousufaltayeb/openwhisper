"""Application styling kept separate from behavior."""

APP_STYLESHEET = """
QWidget {
    color: #e9edf5;
    background: #10131a;
    font-family: "Noto Sans Arabic", "IBM Plex Sans", "DejaVu Sans", sans-serif;
    font-size: 13px;
}
QMainWindow, QDialog { background: #10131a; }
QFrame#Sidebar { background: #0b0e14; border-right: 1px solid #242a36; }
QLabel#Brand { font-size: 20px; font-weight: 700; color: #ffffff; }
QLabel#Eyebrow { color: #7f8ca3; font-size: 11px; font-weight: 600; }
QLabel#PageTitle { font-size: 25px; font-weight: 700; color: #ffffff; }
QLabel#Muted { color: #8f9aae; }
QLabel#StatusPill {
    background: #19202c; color: #b8c2d4; border: 1px solid #293346;
    border-radius: 12px; padding: 5px 10px;
}
QPushButton {
    background: #1c2330; border: 1px solid #303a4c; border-radius: 8px;
    padding: 8px 13px; min-height: 18px;
}
QPushButton:hover { background: #252e3d; border-color: #44516a; }
QPushButton:pressed { background: #151b25; }
QPushButton:disabled { color: #626b7b; background: #151922; border-color: #242a35; }
QPushButton#Primary {
    color: #07110d; background: #67e8b2; border-color: #67e8b2; font-weight: 700;
}
QPushButton#Primary:hover { background: #85f0c3; }
QPushButton#Record {
    background: #f36b72; border: 0; border-radius: 36px; min-width: 72px;
    min-height: 72px; max-width: 72px; max-height: 72px; font-size: 23px;
}
QPushButton#Record[recording="true"] { background: #67e8b2; }
QPushButton#Nav {
    text-align: left; background: transparent; border: 0; color: #9ba6b8;
    padding: 10px 12px;
}
QPushButton#Nav:checked { color: #ffffff; background: #1a202b; border-left: 2px solid #67e8b2; }
QLineEdit, QComboBox, QSpinBox, QTextEdit {
    color: #f2f4f8; background: #151a24; border: 1px solid #2c3443;
    border-radius: 7px; padding: 7px;
}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border-color: #67e8b2; }
QListWidget, QTableWidget {
    background: #121720; border: 1px solid #242c3a; border-radius: 9px;
    alternate-background-color: #151b25; gridline-color: #242c3a;
}
QListWidget::item { padding: 10px; border-bottom: 1px solid #222a37; }
QListWidget::item:selected { background: #202938; color: #ffffff; }
QHeaderView::section {
    color: #919caf; background: #151a23; border: 0; border-bottom: 1px solid #2a3240;
    padding: 8px; font-weight: 600;
}
QGroupBox {
    border: 1px solid #293140; border-radius: 10px; margin-top: 15px;
    padding: 15px 12px 12px;
}
QGroupBox::title { color: #dce2ec; subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QScrollArea { border: 0; }
QStatusBar { color: #8591a5; background: #0d1016; border-top: 1px solid #232a36; }
QWidget[reducedMotion="true"] { qproperty-toolTipDuration: 0; }
"""
