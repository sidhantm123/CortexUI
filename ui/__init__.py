"""UI package. MainWindow is imported lazily so light modules (e.g. ui.theme) load without pyqtgraph."""


def __getattr__(name):
    if name == "MainWindow":
        from .main_window import MainWindow
        return MainWindow
    raise AttributeError(name)
