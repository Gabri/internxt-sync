from textual.screen import ModalScreen
from textual.widgets import Label, Button, Checkbox, Tree
from textual.containers import Vertical, Horizontal
from textual import on, events
import os

class LoginScreen(ModalScreen):
    """Screen to force login."""
    def compose(self):
        yield Vertical(
            Label("InterNxt not logged in or CLI not found."),
            Label("Please log in using the browser window that will open."),
            Horizontal(
                Button("Login", id="login_btn"),
                Button("Quit", id="quit_btn"),
                classes="button_row"
            ),
            classes="modal_dialog"
        )

    @on(Button.Pressed, "#login_btn")
    def on_login(self):
        self.dismiss(True)

    @on(Button.Pressed, "#quit_btn")
    def on_quit(self):
        self.app.exit()

class SyncOptionsScreen(ModalScreen):
    """Screen to configure sync options."""
    def __init__(self, message):
        super().__init__()
        self.message = message

    def compose(self):
        yield Vertical(
            Label(self.message),
            Checkbox("Exclude hidden files/folders (.*)", value=True, id="exclude_hidden"),
            Checkbox("Zip content before upload", value=False, id="zip_mode"),
            Horizontal(
                Button("Start Sync", variant="success", id="start"),
                Button("Cancel", variant="error", id="cancel"),
                classes="button_row"
            ),
            classes="modal_dialog"
        )

    @on(Button.Pressed)
    def action(self, event):
        if event.button.id == "start":
            exclude_hidden = self.query_one("#exclude_hidden", Checkbox).value
            zip_mode = self.query_one("#zip_mode", Checkbox).value
            self.dismiss((True, exclude_hidden, zip_mode))
        else:
            self.dismiss((False, False, False))

class DeletionConfirmScreen(ModalScreen):
    def __init__(self, deletions):
        super().__init__()
        self.deletions = deletions

    def compose(self):
        yield Vertical(
            Label("The following items exist remotely but NOT locally."),
            Label("Select items to DELETE from remote (Space to toggle):"),
            Tree("Remote Deletions", id="del_tree", classes="del_list"),
            Horizontal(
                Button("Confirm Sync", variant="error", id="confirm"),
                Button("Cancel", variant="primary", id="cancel"),
                classes="button_row"
            ),
            classes="modal_dialog_large"
        )

    def on_mount(self):
        tree = self.query_one("#del_tree", Tree)
        tree.show_root = False
        nodes = {}
        # All items are pre-selected for deletion
        for path in sorted(self.deletions):
            parts = path.strip("/").split("/")
            current_path = ""
            parent_node = tree.root
            for i, part in enumerate(parts):
                current_path = os.path.join(current_path, part)
                if current_path not in nodes:
                    is_dir = (i < len(parts) - 1) or (path.endswith("/"))
                    node = parent_node.add("", data={"path": current_path, "is_dir": is_dir, "selected": True})
                    self._update_node_label(node)
                    nodes[current_path] = node
                parent_node = nodes[current_path]

    def on_key(self, event: events.Key):
        if event.key == "space":
            tree = self.query_one("#del_tree", Tree)
            if tree.cursor_node:
                self.toggle_selection(tree.cursor_node)
                event.stop()

    def toggle_selection(self, node):
        """Toggles the selection state of a node and its children."""
        node.data["selected"] = not node.data["selected"]
        self._update_node_label(node)

        if node.data["is_dir"]:
            for child in node.children:
                self._set_child_selection(child, node.data["selected"])
        
        if node.parent and node.parent.data: # Do not update root
             self._update_parent_selection(node.parent)

    def _set_child_selection(self, node, selected):
        """Recursively sets the selection state for a node and its children."""
        node.data["selected"] = selected
        self._update_node_label(node)
        for child in node.children:
            self._set_child_selection(child, selected)

    def _update_parent_selection(self, node):
        """Recursively updates the selection state of parent nodes."""
        if all(child.data["selected"] for child in node.children):
            node.data["selected"] = True
        else:
            node.data["selected"] = False
        self._update_node_label(node)
        if node.parent and node.parent.data: # Do not update root
            self._update_parent_selection(node.parent)

    def _update_node_label(self, node):
        """Updates the node's label to show its selection state."""
        selected = node.data["selected"]
        is_dir = node.data["is_dir"]
        name = os.path.basename(node.data["path"])
        icon = "📁" if is_dir else "📄"
        prefix = "☑" if selected else "☐"
        node.label = f"{prefix} {icon} {name}"

    @on(Button.Pressed, "#confirm")
    def confirm(self):
        """Dismisses the screen, returning the list of paths to delete."""
        tree = self.query_one("#del_tree", Tree)
        selected_paths = set()

        def collect_selected_paths(node):
            # If a node is selected and it's a directory, we can add its path and stop recursing
            if node.data and node.data["selected"]:
                selected_paths.add(node.data["path"])
                return

            # If a node is not selected, but some of its children might be, we need to check them
            for child in node.children:
                collect_selected_paths(child)

        for node in tree.root.children:
            collect_selected_paths(node)
            
        self.dismiss(list(selected_paths))

    @on(Button.Pressed, "#cancel")
    def cancel(self):
        """Dismisses the screen without returning any paths."""
        self.dismiss(None)

class ConfirmScreen(ModalScreen):
    def __init__(self, message):
        super().__init__()
        self.message = message

    def compose(self):
        yield Vertical(
            Label(self.message),
            Horizontal(
                Button("Yes", variant="success", id="yes"),
                Button("No", variant="error", id="no"),
                classes="button_row"
            ),
            classes="modal_dialog"
        )

    @on(Button.Pressed)
    def action(self, event):
        if event.button.id == "yes":
            self.dismiss(True)
        else:
            self.dismiss(False)
