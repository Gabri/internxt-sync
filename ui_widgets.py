from textual.widgets import Tree, Input, Label, ProgressBar
from textual.containers import Vertical, Horizontal
from textual.message import Message
from textual import events
from rich.text import Text
import os

class FileSystemTree(Tree):
    """
    A Tree used as a flat file list (Norton Commander style).
    It displays the contents of 'current_path'.
    Supports multi-selection with SHIFT key.
    """
    def __init__(self, *args, **kwargs):
        super().__init__("root", *args, **kwargs)
        self.current_path = "/"
        self.is_remote = False
        self.app_ref = None # To call methods on app
        self.selected_indices = set()  # Track multi-selected items
        self.shift_anchor = None  # Starting point for shift selection

    def on_mount(self):
        self.show_root = False # Hide the technical root
        self.guide_depth = 1

    def on_key(self, event: events.Key):
        key = event.key
        
        is_shift = getattr(event, 'shift', False) or "shift+" in key
        is_up = key == "up" or key == "shift+up"
        is_down = key == "down" or key == "shift+down"

        if is_up:
            if self.cursor_line == 0 and not is_shift:
                self.post_message(self.FocusInput(self.id))
                event.stop()
                return
            
            # Handle SHIFT+UP for multi-selection
            if is_shift:
                if self.cursor_line > 0:
                    if self.shift_anchor is None:
                        self.shift_anchor = self.cursor_line
                    # Move cursor up
                    new_line = self.cursor_line - 1
                    # Select range from anchor to new position
                    self._select_range(self.shift_anchor, new_line)
                    self.cursor_line = new_line
                    self.refresh()
                event.prevent_default()
                event.stop()
                return
        
        elif is_down:
            # Handle SHIFT+DOWN for multi-selection
            if is_shift:
                if self.cursor_line < len(self.root.children) - 1:
                    if self.shift_anchor is None:
                        self.shift_anchor = self.cursor_line
                    # Move cursor down
                    new_line = self.cursor_line + 1
                    # Select range from anchor to new position
                    self._select_range(self.shift_anchor, new_line)
                    self.cursor_line = new_line
                    self.refresh()
                event.prevent_default()
                event.stop()
                return
        
        if not is_shift and (is_up or is_down):
            self.shift_anchor = None
            if self.selected_indices:
                self._clear_multi_selection_styles()
                self.selected_indices.clear()

    def _select_range(self, start, end):
        """Select all items in range from start to end (inclusive)"""
        if self.selected_indices:
            self._clear_multi_selection_styles()
        self.selected_indices.clear()
        
        if start <= end:
            for i in range(start, end + 1):
                self.selected_indices.add(i)
        else:
            for i in range(end, start + 1):
                self.selected_indices.add(i)
        self._apply_multi_selection_styles()

    def _apply_multi_selection_styles(self):
        for idx in self.selected_indices:
            if idx < len(self.root.children):
                node = self.root.children[idx]
                if node.data is None:
                    node.data = {}
                
                if "original_label" not in node.data:
                    label_str = node.label.plain if isinstance(node.label, Text) else str(node.label)
                    node.data["original_label"] = label_str
                
                node.label = Text(node.data["original_label"], style="reverse")

    def _clear_multi_selection_styles(self):
        for idx in self.selected_indices:
            if idx < len(self.root.children):
                node = self.root.children[idx]
                if node.data and "original_label" in node.data:
                    node.label = Text(node.data["original_label"])

    def get_selected_items(self):
        """Get list of selected items (data dicts)"""
        if not self.selected_indices:
            # If no multi-selection, return current cursor item
            if self.cursor_line >= 0 and self.cursor_line < len(self.root.children):
                node = self.root.children[self.cursor_line]
                return [node.data] if node.data else []
            return []
        
        # Return all selected items
        items = []
        for idx in sorted(self.selected_indices):
            if idx < len(self.root.children):
                node = self.root.children[idx]
                if node.data:
                    items.append(node.data)
        return items

    def clear_selection(self):
        self._clear_multi_selection_styles()
        self.selected_indices.clear()
        self.shift_anchor = None
        self.refresh()

    class FocusInput(Message):
        def __init__(self, tree_id):
            super().__init__()
            self.tree_id = tree_id

class Pane(Vertical):
    """A pane containing an input, a tree, and a status bar with progress."""
    def __init__(self, title, id, **kwargs):
        super().__init__(id=id, **kwargs)
        self.title = title

    def compose(self):
        yield Input(placeholder=self.title, id=f"{self.id}_input")
        yield FileSystemTree(self.title, id=f"{self.id}_tree")
        with Vertical(classes="pane_footer"):
            yield Label("Files: 0 | Size: 0 B", id=f"{self.id}_stats")
            yield ProgressBar(id=f"{self.id}_progress", show_eta=False, show_percentage=True)
