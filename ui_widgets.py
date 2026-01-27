from textual.widgets import Tree, Input, Label, ProgressBar
from textual.containers import Vertical, Horizontal
from textual.message import Message
from textual import events
import os

class FileSystemTree(Tree):
    """
    A Tree used as a flat file list (Norton Commander style).
    It displays the contents of 'current_path'.
    """
    def __init__(self, *args, **kwargs):
        super().__init__("root", *args, **kwargs)
        self.current_path = "/"
        self.is_remote = False
        self.app_ref = None # To call methods on app

    def on_mount(self):
        self.show_root = False # Hide the technical root
        self.guide_depth = 1

    def on_key(self, event: events.Key):
        if event.key == "up":
            if self.cursor_line == 0:
                self.post_message(self.FocusInput(self.id))
                event.stop()

    class FocusInput(Message):
        def __init__(self, tree_id):
            super().__init__()
            self.tree_id = tree_id

class Pane(Vertical):
    """A pane containing an input, a tree, and a status bar."""
    def __init__(self, title, id, **kwargs):
        super().__init__(id=id, **kwargs)
        self.title = title

    def compose(self):
        yield Input(placeholder=self.title, id=f"{self.id}_input")
        yield FileSystemTree(self.title, id=f"{self.id}_tree")
        with Horizontal(classes="pane_footer"):
            yield Label("Files: 0 | Size: 0 B", id=f"{self.id}_stats")
            progress = ProgressBar(id=f"{self.id}_progress", show_eta=False, show_percentage=False)
            progress.update(total=100, progress=0)
            yield progress
