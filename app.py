from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Tree, Label, Log, Button, SelectionList, Input
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.worker import Worker, get_current_worker
from textual import on, work, events
from textual.message import Message

import os
import time
import requests
from internxt_client import InternxtClient
from sync_logic import SyncEngine

# --- Screens ---

class LoginScreen(ModalScreen):
    """Screen to force login."""
    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("InterNxt not logged in or CLI not found."),
            Label("Please log in using the browser window that will open."),
            Button("Login", id="login_btn"),
            Button("Quit", id="quit_btn"),
            classes="modal_dialog"
        )

    @on(Button.Pressed, "#login_btn")
    def on_login(self):
        self.dismiss(True)

    @on(Button.Pressed, "#quit_btn")
    def on_quit(self):
        self.app.exit()

class DeletionConfirmScreen(ModalScreen):
    def __init__(self, deletions):
        super().__init__()
        self.deletions = deletions

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("The following items exist remotely but NOT locally."),
            Label("Select items to DELETE from remote (Space to toggle):"),
            SelectionList(*[(path, path, False) for path in self.deletions], id="del_list"),
            Horizontal(
                Button("Confirm Sync", variant="error", id="confirm"),
                Button("Cancel", variant="primary", id="cancel"),
                classes="button_row"
            ),
            classes="modal_dialog_large"
        )

    @on(Button.Pressed, "#confirm")
    def confirm(self):
        selection_list = self.query_one("#del_list", SelectionList)
        self.dismiss(selection_list.selected)

    @on(Button.Pressed, "#cancel")
    def cancel(self):
        self.dismiss(None)

class ConfirmScreen(ModalScreen):
    def __init__(self, message):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
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

# --- Custom Widgets ---

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

class InternxtSyncApp(App):
    CSS = """
    Screen {
        layers: base modal;
    }
    .modal_dialog {
        padding: 2;
        border: solid green;
        background: $surface;
        width: 60;
        height: auto;
        align: center middle;
    }
    .modal_dialog_large {
        padding: 2;
        border: solid red;
        background: $surface;
        width: 80%;
        height: 80%;
        align: center middle;
    }
    .button_row {
        align: center middle;
        height: auto;
        margin-top: 1;
    }
    Button {
        margin: 1;
    }
    #left_pane, #right_pane {
        width: 50%;
        height: 100%;
        border: solid blue;
    }
    #left_pane:focus-within {
        border: double blue;
    }
    #right_pane:focus-within {
        border: double green;
    }
    Input {
        dock: top;
        margin-bottom: 1;
        border: transparent;
        background: $accent;
        color: $text;
    }
    Input:focus {
        border: solid yellow;
    }
    Log {
        height: 20%;
        dock: bottom;
        border-top: solid $secondary;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("tab", "toggle_pane", "Switch Pane"),
        ("s", "sync", "Sync Local -> Remote"),
        ("d", "download", "Download Remote -> Local"),
        ("r", "refresh", "Refresh View"),
        ("ctrl+l", "focus_path", "Edit Path"),
    ]

    def __init__(self):
        super().__init__()
        self.client = InternxtClient()
        self.sync_engine = SyncEngine(self.client)
        self.local_path = os.getcwd()
        self.remote_path = "/"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Horizontal(
            Vertical(
                Input(self.local_path, id="local_input"),
                FileSystemTree("Local", id="local_tree"),
                id="left_pane"
            ),
            Vertical(
                Input(self.remote_path, id="remote_input"),
                FileSystemTree("Remote", id="remote_tree"),
                id="right_pane"
            )
        )
        yield Log()
        yield Footer()

    def on_mount(self):
        # Configure trees
        self.query_one("#local_tree").is_remote = False
        self.query_one("#local_tree").app_ref = self
        
        self.query_one("#remote_tree").is_remote = True
        self.query_one("#remote_tree").app_ref = self

        # Load local immediately
        self.refresh_local(self.local_path)

        self.log_message("Checking Internxt status...")
        if not self.client.check_login():
            self.push_screen(LoginScreen(), self.after_login)
        else:
            self.start_webdav_and_load()

    def after_login(self, should_login):
        if should_login:
            self.client.login()
            self.notify("Please complete login in browser.", severity="warning")
            self.start_webdav_and_load()
        else:
            self.exit()

    @work(exclusive=True, thread=True)
    def start_webdav_and_load(self):
        self.log_message("Checking WebDAV connectivity...")
        
        # Check if already running
        if self.client.is_webdav_active():
             self.log_message("WebDAV already active.")
        else:
             self.log_message("Starting WebDAV server...")
             self.client.start_webdav()
        
        # Wait for WebDAV to be ready
        self.log_message("Waiting for WebDAV response...")
        max_retries = 30
        for i in range(max_retries):
            try:
                # Try to list root to check if ready
                if self.client.list_remote("/") is not None:
                    self.log_message("WebDAV Connected!")
                    self.call_from_thread(self.refresh_remote, self.remote_path)
                    break
            except Exception as e:
                # self.log_message(f"Wait... {e}")
                time.sleep(1)
                if i == max_retries - 1:
                    self.log_message("WebDAV timeout. Try refreshing manually (r).")
        
    def load_panes(self):
        # Deprecated: called individually now
        pass

    def on_unmount(self):
        # We don't stop webdav anymore to allow persistence/speed?
        # Or we check if we started it.
        # User requested ensure disabled on exit.
        self.client.stop_webdav()

    # --- Actions & Navigation ---

    def action_toggle_pane(self):
        left = self.query_one("#left_pane")
        right = self.query_one("#right_pane")
        
        # Check focus and switch
        if left.has_focus:
            self.query_one("#remote_tree").focus()
        else:
            self.query_one("#local_tree").focus()

    def action_focus_path(self):
        # Focus the input of the active pane
        if self.query_one("#left_pane").has_focus:
            self.query_one("#local_input").focus()
        else:
            self.query_one("#remote_input").focus()

    @on(FileSystemTree.FocusInput)
    def on_tree_request_focus(self, message):
        if message.tree_id == "local_tree":
            self.query_one("#local_input").focus()
        else:
            self.query_one("#remote_input").focus()

    @on(Input.Submitted)
    def on_path_submit(self, event):
        inp = event.input
        new_path = inp.value
        if inp.id == "local_input":
            if os.path.exists(new_path) and os.path.isdir(new_path):
                self.local_path = new_path
                self.refresh_local(new_path)
                self.query_one("#local_tree").focus()
            else:
                self.notify(f"Invalid local directory: {new_path}", severity="error")
        elif inp.id == "remote_input":
            self.remote_path = new_path
            self.refresh_remote(new_path)
            self.query_one("#remote_tree").focus()

    def action_refresh(self):
        self.refresh_local(self.local_path)
        self.refresh_remote(self.remote_path)

    # --- Tree Population ---

    def refresh_local(self, path):
        self.local_path = path
        self.query_one("#local_input").value = path
        tree = self.query_one("#local_tree")
        tree.clear()
        
        # Add ".."
        parent = os.path.dirname(path)
        if parent != path: # Not root
            tree.root.add("..", data={"type": "dir", "path": parent, "is_up": True})

        try:
            # Sort: Dirs then Files
            entries = os.scandir(path)
            dirs = []
            files = []
            for entry in entries:
                if entry.is_dir():
                    dirs.append(entry)
                else:
                    files.append(entry)
            
            dirs.sort(key=lambda e: e.name.lower())
            files.sort(key=lambda e: e.name.lower())

            for d in dirs:
                tree.root.add(f"📁 {d.name}", data={"type": "dir", "path": d.path, "is_up": False}, allow_expand=False)
            for f in files:
                tree.root.add(f"📄 {f.name} ({f.stat().st_size}b)", data={"type": "file", "path": f.path}, allow_expand=False)
                
            tree.root.expand()
        except PermissionError:
            self.notify("Permission Denied", severity="error")
        except Exception as e:
            self.log_message(f"Local Error: {e}")

    @work(thread=True)
    def refresh_remote(self, path):
        # Update input immediately for feedback
        self.call_from_thread(self.update_remote_input, path)
        
        try:
            items = self.client.list_remote(path)
        except Exception as e:
            self.log_message(f"Remote List Error: {e}")
            return

        self.call_from_thread(self.populate_remote_tree, path, items)

    def update_remote_input(self, path):
        self.query_one("#remote_input").value = path
        self.remote_path = path

    def populate_remote_tree(self, path, items):
        tree = self.query_one("#remote_tree")
        tree.clear()
        
        # Add ".."
        if path != "/" and path != "":
            parent = os.path.dirname(path.rstrip("/"))
            if not parent.startswith("/"): parent = "/" + parent # Fix dirname behavior
            # On windows os.path.dirname("/") might be "/" but let's ensure.
            if parent == "" or parent == "/": parent = "/"
            
            tree.root.add("..", data={"type": "dir", "path": parent, "is_up": True})

        if items:
            # Sort
            items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
            
            for item in items:
                name = item['name']
                if item['is_dir']:
                    tree.root.add(f"📁 {name}", data={"type": "dir", "path": item['path'], "is_up": False}, allow_expand=False)
                else:
                    tree.root.add(f"📄 {name} ({item['size']}b)", data={"type": "file", "path": item['path']}, allow_expand=False)
        
        tree.root.expand()

    # --- Interaction ---

    @on(Tree.NodeSelected)
    def on_node_selected(self, event):
        node = event.node
        data = node.data
        if not data: return # Root node

        tree = event.control
        
        if data["type"] == "dir":
            # Navigation
            new_path = data["path"]
            if tree.id == "local_tree":
                self.refresh_local(new_path)
            else:
                self.refresh_remote(new_path)
        else:
            # File Action
            if tree.id == "remote_tree":
                # Download
                self.action_download_item(data["path"])
            else:
                # Local File selected - maybe future preview?
                pass

    def action_download_item(self, remote_path):
        filename = os.path.basename(remote_path)
        local_target = os.path.join(self.local_path, filename)
        
        def do_download(confirm):
            if confirm:
                self.log_message(f"Downloading {remote_path} to {local_target}...")
                self.run_download(remote_path, local_target)
        
        self.push_screen(ConfirmScreen(f"Download {filename} here?"), do_download)

    @work(thread=True)
    def run_download(self, remote, local):
        try:
            self.client.download_file(remote, local)
            self.log_message("Download complete.")
            self.call_from_thread(self.refresh_local, self.local_path)
        except Exception as e:
            self.log_message(f"Download error: {e}")

    def action_sync(self):
        def start_sync_process(confirm):
            if confirm:
                self.log_message(f"Syncing {self.local_path} -> {self.remote_path}")
                self.run_sync_analysis(self.local_path, self.remote_path)

        self.push_screen(ConfirmScreen(f"Sync local content to remote folder?"), start_sync_process)

    @work(thread=True)
    def run_sync_analysis(self, local_root, remote_root):
        self.log_message("Scanning local files...")
        local_items = self.sync_engine.scan_local(local_root)
        
        self.log_message("Scanning remote files...")
        try:
            remote_items = self.sync_engine.scan_remote(remote_root)
        except Exception as e:
            self.log_message(f"Sync Scan Error: {e}")
            return
        
        to_upload, to_create, to_delete = self.sync_engine.compare(local_items, remote_items)
        
        self.log_message(f"Analysis: {len(to_upload)} uploads, {len(to_create)} dirs, {len(to_delete)} deletions.")

        if to_delete:
            self.call_from_thread(self.prompt_deletions, to_delete, to_upload, to_create, local_root, remote_root)
        else:
            self.run_sync_execution(to_upload, to_create, [], local_root, remote_root)

    def prompt_deletions(self, to_delete, to_upload, to_create, local_root, remote_root):
        def on_confirm(selected_deletions):
            if selected_deletions is None:
                self.log_message("Sync cancelled.")
                return
            self.run_worker(self.run_sync_execution(to_upload, to_create, selected_deletions, local_root, remote_root))

        self.push_screen(DeletionConfirmScreen(to_delete), on_confirm)

    @work(thread=True)
    def run_sync_execution(self, to_upload, to_create, to_delete, local_root, remote_root):
        # Create Dirs
        for rel_path in to_create:
            remote_path = os.path.join(remote_root, rel_path).replace("\\", "/") 
            self.log_message(f"Creating dir: {rel_path}")
            try:
                self.client.create_directory(remote_path)
            except Exception as e:
                self.log_message(f"Error creating dir {rel_path}: {e}")

        # Uploads
        for abs_path, rel_path in to_upload:
            remote_path = os.path.join(remote_root, rel_path).replace("\\", "/")
            self.log_message(f"Uploading: {rel_path}")
            try:
                self.client.upload_file(abs_path, remote_path)
            except Exception as e:
                self.log_message(f"Error uploading {rel_path}: {e}")

        # Deletions
        for rel_path in to_delete:
            remote_path = os.path.join(remote_root, rel_path).replace("\\", "/")
            self.log_message(f"Deleting: {rel_path}")
            try:
                self.client.delete_item(remote_path)
            except Exception as e:
                self.log_message(f"Error deleting {rel_path}: {e}")

        self.log_message("Sync complete.")
        self.call_from_thread(self.refresh_remote, remote_root)

    def log_message(self, msg):
        self.query_one(Log).write_line(f"[{time.strftime('%H:%M:%S')}] {msg}")

if __name__ == "__main__":
    app = InternxtSyncApp()
    app.run()
