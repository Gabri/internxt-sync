from textual.widgets import Tree, Input, Label, ProgressBar, ListView, ListItem, Static, Footer
from textual.containers import Vertical, Horizontal, Container
from textual.message import Message
from textual import events
from textual.reactive import reactive
from rich.text import Text
import os
import re
import glob as glob_module


class FileSystemTree(Tree):
    """
    A Tree used as a flat file list (Norton Commander style).
    It displays the contents of 'current_path'.
    Supports multi-selection with SHIFT key.
    Supports fuzzy filtering with live search.
    """

    filter_query = reactive("")

    def __init__(self, *args, **kwargs):
        super().__init__("root", *args, **kwargs)
        self.current_path = "/"
        self.is_remote = False
        self.app_ref = None  # To call methods on app
        self.selected_indices = set()  # Track multi-selected items
        self.shift_anchor = None  # Starting point for shift selection
        self._all_nodes_data = []  # Store all nodes for filtering
        self._filter_input = None  # Reference to filter input widget

    def on_mount(self):
        self.show_root = False  # Hide the technical root
        self.guide_depth = 1

    def set_filter_input(self, input_widget):
        """Set reference to the filter input widget."""
        self._filter_input = input_widget

    def store_nodes_data(self, nodes_data):
        """Store the original list of all nodes for filtering."""
        self._all_nodes_data = nodes_data

    def watch_filter_query(self, query):
        """Called when filter_query changes - apply filter."""
        self.apply_filter(query)

    def apply_filter(self, query: str):
        """Apply fuzzy filter to the tree nodes."""
        if not query:
            # Restore all nodes
            self._restore_all_nodes()
            return

        query_lower = query.lower()
        # Split query into characters for fuzzy matching
        query_chars = list(query_lower)

        # Filter nodes that contain all characters in order (fuzzy match)
        filtered_indices = []
        for idx, node_data in enumerate(self._all_nodes_data):
            label = node_data.get("label", "").lower()
            # Fuzzy match: all query characters must appear in order in label
            if self._fuzzy_match(label, query_chars):
                filtered_indices.append(idx)

        # Rebuild tree with filtered nodes
        self._rebuild_tree_with_indices(filtered_indices)

    def _fuzzy_match(self, text: str, query_chars: list) -> bool:
        """
        Check if all query characters appear in order in text.
        This is a simple fuzzy match algorithm.
        """
        if not query_chars:
            return True
        if not text:
            return False

        text_idx = 0
        for char in query_chars:
            # Find this character in remaining text
            found = False
            while text_idx < len(text):
                if text[text_idx] == char:
                    found = True
                    text_idx += 1
                    break
                text_idx += 1
            if not found:
                return False
        return True

    def _fuzzy_score(self, text: str, query: str) -> int:
        """
        Calculate fuzzy match score. Higher is better.
        Bonus for: consecutive matches, match at start of word, match at start of string.
        """
        if not query:
            return 1

        text_lower = text.lower()
        query_lower = query.lower()
        score = 0
        text_idx = 0

        for char in query_lower:
            # Find char in remaining text
            found_pos = text_lower.find(char, text_idx)
            if found_pos == -1:
                return 0  # Character not found

            # Bonus for consecutive matches
            if found_pos == text_idx:
                score += 10
            else:
                score += 1

            # Bonus for match at start of word
            if found_pos == 0 or text_lower[found_pos - 1] in " ._-/":
                score += 5

            text_idx = found_pos + 1

        # Penalty for length difference
        score -= (len(text) - len(query)) * 0.1

        return int(max(1, score))

    def _restore_all_nodes(self):
        """Restore all nodes from stored data."""
        if not self._all_nodes_data:
            return

        self.clear()
        for node_data in self._all_nodes_data:
            self.root.add(
                node_data.get("label", ""),
                data=node_data.get("data"),
                allow_expand=False,
            )
        self.root.expand()

        # Restore cursor position if valid
        if self.cursor_line >= len(self.root.children):
            self.cursor_line = 0 if self.root.children else -1
        self.refresh()

    def _rebuild_tree_with_indices(self, indices):
        """Rebuild tree showing only nodes at given indices."""
        if not indices or not self._all_nodes_data:
            return

        self.clear()
        for idx in indices:
            if idx < len(self._all_nodes_data):
                node_data = self._all_nodes_data[idx]
                self.root.add(
                    node_data.get("label", ""),
                    data=node_data.get("data"),
                    allow_expand=False,
                )
        self.root.expand()

        # Reset cursor to first item
        if self.root.children:
            self.cursor_line = 0
        self.refresh()

    def clear_filter(self):
        """Clear the current filter."""
        self.filter_query = ""
        if self._filter_input:
            self._filter_input.value = ""

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

class CustomFooter(Footer):

    def render(self) -> str:
        conditional_bindings = {'/', 'escape'}
        is_tree_focused = getattr(self.app, 'is_tree_focused', lambda: True)()

        bindings = []
        for active_binding in self.app.active_bindings.values():
            binding = active_binding.binding
            if binding.key in conditional_bindings and not is_tree_focused:
                continue
            bindings.append(f"[{binding.key}] {binding.description}")

        if len(bindings) > 6:
            mid = len(bindings) // 2
            line1 = "  ".join(bindings[:mid])
            line2 = "  ".join(bindings[mid:])
            return f"{line1}\n{line2}"
        else:
            return "  ".join(bindings)


class PathInputWithAutocomplete(Input):

    def __init__(self, enable_autocomplete=True, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dropdown = None
        self._suggestions = []
        self._selected_index = -1
        self._enable_autocomplete = enable_autocomplete

    def on_mount(self):
        if self._enable_autocomplete:
            self._create_dropdown()
            self._mount_dropdown()

    def _create_dropdown(self):
        from textual.containers import Container
        self._dropdown = Container(
            ListView(id=f"{self.id}_dropdown_list"),
            id=f"{self.id}_dropdown",
            classes="path_dropdown"
        )
        self._dropdown.display = False

    def _mount_dropdown(self):
        if self._dropdown and self.parent:
            try:
                self.parent.mount(self._dropdown, before=self)
            except Exception:
                pass

    def on_input_changed(self, event):
        if not self._enable_autocomplete:
            return
        value = event.value
        if len(value) < 1:
            self._hide_dropdown()
            return

        if "/" in value:
            dir_part = os.path.dirname(value)
            name_part = os.path.basename(value)
        else:
            dir_part = "."
            name_part = value

        if dir_part.startswith("~"):
            dir_part = os.path.expanduser(dir_part)

        try:
            if os.path.isdir(dir_part):
                suggestions = []
                for entry in os.scandir(dir_part):
                    if entry.is_dir() and entry.name.lower().startswith(name_part.lower()):
                        full_path = os.path.join(dir_part, entry.name) + "/"
                        suggestions.append((entry.name, full_path))

                suggestions.sort(key=lambda x: x[0].lower())
                self._suggestions = suggestions[:20]
                self._show_suggestions()
            else:
                self._hide_dropdown()
        except (OSError, PermissionError):
            self._hide_dropdown()

    def _show_suggestions(self):
        if not self._suggestions:
            self._hide_dropdown()
            return

        list_view = self._dropdown.query_one(ListView)
        list_view.clear()

        for name, full_path in self._suggestions:
            list_view.append(ListItem(Label(f"📁 {name}")))

        self._dropdown.display = True
        self._selected_index = 0
        self._highlight_selection()

    def _hide_dropdown(self):
        if self._dropdown:
            self._dropdown.display = False
        self._selected_index = -1
        self._suggestions = []

    def _highlight_selection(self):
        if not self._dropdown:
            return
        list_view = self._dropdown.query_one(ListView)
        if 0 <= self._selected_index < len(self._suggestions):
            list_view.index = self._selected_index

    def move_selection_up(self):
        if self._dropdown and self._dropdown.display:
            if self._selected_index > 0:
                self._selected_index -= 1
                self._highlight_selection()
            return True
        return False

    def move_selection_down(self):
        if self._dropdown and self._dropdown.display:
            if self._selected_index < len(self._suggestions) - 1:
                self._selected_index += 1
                self._highlight_selection()
            return True
        return False

    def accept_suggestion(self):
        if self._dropdown and self._dropdown.display and self._suggestions:
            if 0 <= self._selected_index < len(self._suggestions):
                _, full_path = self._suggestions[self._selected_index]
                self.value = full_path
                self.cursor_position = len(full_path)
                self._hide_dropdown()
                return True
            elif self._suggestions:
                _, full_path = self._suggestions[0]
                self.value = full_path
                self.cursor_position = len(full_path)
                self._hide_dropdown()
                return True
        return False

    def on_key(self, event: events.Key):
        if not self._enable_autocomplete or not self._dropdown:
            return
        if self._dropdown.display:
            if event.key == "up":
                if self.move_selection_up():
                    event.stop()
            elif event.key == "down":
                if self.move_selection_down():
                    event.stop()
            elif event.key == "tab":
                if self.accept_suggestion():
                    event.stop()
            elif event.key == "escape":
                self._hide_dropdown()
                event.stop()


class Pane(Vertical):
    def __init__(self, title, id, **kwargs):
        super().__init__(id=id, **kwargs)
        self.title = title

    def compose(self):
        enable_autocomplete = "left" in self.id
        yield PathInputWithAutocomplete(enable_autocomplete=enable_autocomplete, placeholder=self.title, id=f"{self.id}_input")
        yield Input(placeholder="Filter...", id=f"{self.id}_filter", classes="filter_input")
        yield FileSystemTree(self.title, id=f"{self.id}_tree")
        with Vertical(classes="pane_footer"):
            yield Label("Files: 0 | Size: 0 B", id=f"{self.id}_stats")
            yield ProgressBar(id=f"{self.id}_progress", show_eta=False, show_percentage=True)
