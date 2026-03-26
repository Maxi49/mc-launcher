"""Stylesheet constants for the launcher UI."""

PLAY_BUTTON_STYLE = """
    QPushButton {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #2ea043, stop:1 #238636);
        border: 1px solid #2ea043;
        font-size: 18px;
        font-weight: 700;
        border-radius: 10px;
        padding: 10px 20px;
        color: #ffffff;
    }
    QPushButton:hover {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #3fb950, stop:1 #2ea043);
        border: 1px solid #3fb950;
    }
    QPushButton:disabled {
        background: #1a3a1a;
        color: #5a7a5a;
        border: 1px solid #253525;
    }
"""

MAIN_STYLESHEET = """
    QWidget {
        background-color: #1a1d23;
        color: #e6edf3;
    }
    QScrollArea {
        background-color: #1a1d23;
    }
    QTabWidget::pane {
        border: 1px solid #2d3548;
        border-radius: 6px;
        background: #1a1d23;
        top: -1px;
    }
    QTabBar::tab {
        background: #1e2330;
        border: 1px solid #2d3548;
        padding: 8px 16px;
        margin-right: 2px;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
    }
    QTabBar::tab:selected {
        background: #1a1d23;
        border-bottom: none;
        color: #58a6ff;
    }
    QTabBar::tab:hover {
        background: #252d3b;
    }
    QLineEdit, QPlainTextEdit, QListWidget, QSpinBox, QComboBox {
        background-color: #1e2330;
        border: 1px solid #2d3548;
        padding: 6px;
        border-radius: 6px;
    }
    QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {
        border: 1px solid #58a6ff;
    }
    QComboBox::drop-down {
        border: none;
        padding-right: 8px;
    }
    QComboBox QAbstractItemView {
        background-color: #1e2330;
        border: 1px solid #2d3548;
        selection-background-color: #2a3a4a;
    }
    QPushButton, QToolButton {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #2c3444, stop:1 #252d3b);
        border: 1px solid #3a4250;
        padding: 6px 10px;
        border-radius: 6px;
    }
    QPushButton:hover, QToolButton:hover {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #374050, stop:1 #2c3444);
        border: 1px solid #58a6ff;
    }
    QPushButton:disabled {
        color: #7f8792;
        background-color: #242a33;
        border: 1px solid #2d3548;
    }
    QPushButton#serverButton {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #253350, stop:1 #1e2a42);
        border: 1px solid #2d4a6f;
    }
    QPushButton#serverButton:hover {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #2e3d5e, stop:1 #253350);
        border: 1px solid #58a6ff;
    }
    QGroupBox {
        border: 1px solid #2d3548;
        border-radius: 8px;
        margin-top: 6px;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 8px;
        padding: 0 4px;
        color: #58a6ff;
    }
    QCheckBox { spacing: 6px; }
    QScrollBar:vertical {
        background: #1a1d23;
        width: 10px;
        border-radius: 5px;
    }
    QScrollBar::handle:vertical {
        background: #2d3548;
        border-radius: 5px;
        min-height: 30px;
    }
    QScrollBar::handle:vertical:hover {
        background: #3a4560;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
