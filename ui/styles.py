"""Theme-aware styles for DeadZone Flash Tool."""
from qfluentwidgets import isDarkTheme


def _is_dark():
    """Helper that checks the current theme."""
    try:
        return isDarkTheme()
    except Exception:
        return False


class Styles:
    """Provides stylesheets that automatically adapt to Light/Dark mode."""

    @staticmethod
    def terminal():
        """Terminal always uses a dark background."""
        return """
            QTextEdit {
                background-color: #0c0c0c;
                color: #cccccc;
                font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
                font-size: 13px;
                border: 1px solid #333333;
                border-radius: 8px;
                padding: 10px;
                selection-background-color: #264f78;
            }
        """

    @staticmethod
    def console_input():
        """Command input always uses a dark background."""
        return """
            QLineEdit {
                background-color: #1a1a2e;
                color: #e0e0e0;
                font-family: 'Cascadia Code', 'Consolas', monospace;
                font-size: 13px;
                border: 1px solid #444;
                border-radius: 6px;
                padding: 8px 12px;
            }
            QLineEdit:focus {
                border: 2px solid #0078d4;
            }
        """

    @staticmethod
    def status_connected():
        return """
            QFrame#statusFrame {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #1b5e20, stop:1 #388e3c);
                border-radius: 12px;
                border: 1px solid #4caf50;
            }
            QFrame#statusFrame QLabel {
                color: white;
                background: transparent;
            }
        """

    @staticmethod
    def status_disconnected():
        if _is_dark():
            return """
                QFrame#statusFrame {
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #1e1e2e, stop:1 #2d2d3f);
                    border-radius: 12px;
                    border: 1px solid #444466;
                }
                QFrame#statusFrame QLabel {
                    color: #b0b0c0;
                    background: transparent;
                }
            """
        else:
            return """
                QFrame#statusFrame {
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #eceff1, stop:1 #cfd8dc);
                    border-radius: 12px;
                    border: 1px solid #b0bec5;
                }
                QFrame#statusFrame QLabel {
                    color: #37474f;
                    background: transparent;
                }
            """

    @staticmethod
    def warning_label():
        """Theme-aware subtle warning label."""
        if _is_dark():
            return "color: #ffb74d; font-size: 12px;"
        else:
            return "color: #e65100; font-size: 12px;"

    @staticmethod
    def wipe_checkbox():
        if _is_dark():
            return "color: #ef5350; font-weight: bold;"
        else:
            return "color: #c62828; font-weight: bold;"

    @staticmethod
    def scanned_label():
        if _is_dark():
            return "color: #999;"
        else:
            return "color: #666;"

    @staticmethod
    def checklist_pending():
        if _is_dark():
            return "color: #888;"
        else:
            return "color: #999;"

    @staticmethod
    def checklist_running():
        return "color: #42a5f5; font-weight: bold;"

    @staticmethod
    def checklist_success():
        return "color: #66bb6a; font-weight: bold;"

    @staticmethod
    def checklist_failed():
        return "color: #ef5350; font-weight: bold;"

    @staticmethod
    def status_edl_connected():
        """Connected EDL 9008 status with an orange gradient."""
        return """
            QFrame#edlStatusFrame {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #e65100, stop:1 #f57c00);
                border-radius: 12px;
                border: 1px solid #ff9800;
            }
            QFrame#edlStatusFrame QLabel {
                color: white;
                background: transparent;
            }
        """

    @staticmethod
    def status_edl_disconnected():
        """Disconnected EDL status."""
        if _is_dark():
            return """
                QFrame#edlStatusFrame {
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #1e1e2e, stop:1 #2d2d3f);
                    border-radius: 12px;
                    border: 1px solid #444466;
                }
                QFrame#edlStatusFrame QLabel {
                    color: #b0b0c0;
                    background: transparent;
                }
            """
        else:
            return """
                QFrame#edlStatusFrame {
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #eceff1, stop:1 #cfd8dc);
                    border-radius: 12px;
                    border: 1px solid #b0bec5;
                }
                QFrame#edlStatusFrame QLabel {
                    color: #37474f;
                    background: transparent;
                }
            """
