from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Tree, Label, Log, Button, SelectionList, Input, ProgressBar, Static, Checkbox
from textual.containers import Container, Horizontal, Vertical, Grid
from textual.screen import ModalScreen
from textual.worker import Worker, get_current_worker
from textual import on, work, events
from textual.message import Message
from textual.reactive import reactive

import os
import time
import shutil
import tempfile
import zipfile
import requests
from internxt_client import InternxtClient
from sync_logic import SyncEngine

# --- Screens ---

class SyncOptionsScreen(ModalScreen):
    """Screen to configure sync options."""
    def __init__(self, message):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
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

    """Screen to force login."""
    def compose(self) -> ComposeResult:
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

class Pane(Vertical):
    """A pane containing an input, a tree, and a status bar."""
    def __init__(self, title, id, **kwargs):
        super().__init__(id=id, **kwargs)
        self.title = title

    def compose(self) -> ComposeResult:
        yield Input(placeholder=self.title, id=f"{self.id}_input")
        yield FileSystemTree(self.title, id=f"{self.id}_tree")
        with Horizontal(classes="pane_footer"):
            yield Label("Files: 0 | Size: 0 B", id=f"{self.id}_stats")
            progress = ProgressBar(id=f"{self.id}_progress", show_eta=False, show_percentage=False)
            progress.update(total=100, progress=0)
            yield progress

class InternxtSyncApp(App):
    CSS = """
    Screen {
        layers: base modal;
        background: #1e1e1e;
        color: #d4d4d4;
        align: center middle;
    }
    #app_status_bar {
        height: 1;
        background: #252526;
        color: #888888;
        padding: 0 1;
    }
    .modal_dialog {
        padding: 2 4;
        border: solid #007acc;
        background: #252526;
        width: 50;
        height: auto;
    }
    .modal_dialog_large {
        padding: 1 2;
        border: solid #007acc;
        background: #252526;
        width: 70%;
        height: 70%;
    }
    .button_row {
        align: center middle;
        height: auto;
        margin-top: 1;
        width: 100%;
    }
    Button {
        margin: 0 1;
        height: 3;
        min-width: 20;
        background: #3e3e42;
        color: #ffffff;
        border: none;
    }
    Button:hover {
        background: #007acc;
        color: #ffffff;
    }
    #left_pane, #right_pane {
        width: 50%;
        height: 100%;
        border: solid #333333;
        background: #1e1e1e;
    }
    #left_pane:focus-within {
        border: solid #007acc;
    }
    #right_pane:focus-within {
        border: solid #007acc;
    }
    FileSystemTree {
        height: 1fr;
    }
    Input {
        dock: top;
        margin: 0;
        padding: 0 1;
        border: none;
        background: #2d2d2d;
        color: #cccccc;
        height: 1;
    }
    Input:focus {
        border: none;
        background: #3d3d3d;
        color: #ffffff;
    }
    .pane_footer {
        height: 1;
        background: #252526;
        color: #888888;
        padding: 0 1;
        border-top: solid #333333;
    }
    .pane_footer Label {
        width: 1fr;
    }
    ProgressBar {
        width: 30%;
        height: 1;
        margin-left: 1;
    }
    ProgressBar > .bar--bar {
        background: #007acc;
    }
    ProgressBar > .bar--complete {
        background: #4caf50;
    }
    ProgressBar > .bar--background {
        background: #333333;
    }
    Log {
        height: 20%;
        dock: bottom;
        border-top: solid #333333;
        background: #1e1e1e;
        color: #cccccc;
    }

    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("tab", "toggle_pane", "Switch Pane"),
        ("s", "sync", "Sync Local -> Remote"),
        ("d", "download", "Download Remote -> Local"),
        ("r", "refresh", "Refresh View"),
        ("ctrl+l", "focus_path", "Edit Path"),
        ("z", "calc_size", "Calc Folder Size"),
        ("m", "toggle_mode", "Toggle CLI/WebDAV"),
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
            Pane("Local", id="left_pane"),
            Pane("Remote", id="right_pane")
        )
        with Horizontal(id="app_status_bar"):
            yield Label("Mode: CLI", id="mode_label")
            yield Label(" | Press 'm' to toggle CLI/WebDAV", id="mode_hint")
        log_widget = Log(id="app_log")
        log_widget.can_focus = False
        yield log_widget
        yield Footer()


    def on_mount(self):
        # Disable direct focus on inputs and log
        self.query_one("#left_pane_input").can_focus = False
        self.query_one("#right_pane_input").can_focus = False
        self.query_one("#app_log").can_focus = False

        # Configure trees
        self.query_one("#left_pane_tree").is_remote = False
        self.query_one("#left_pane_tree").app_ref = self
        
        self.query_one("#right_pane_tree").is_remote = True
        self.query_one("#right_pane_tree").app_ref = self

        # Load local immediately - runs in a worker thread
        self.refresh_local(self.local_path)

        self.log_message("Checking Internxt status...")
        check = self.client.check_login()
        self.log_message(f"Login Check Result: {check}")
        
        if not check:
            self.log_message("Not logged in. Showing Login Screen.")
            self.push_screen(LoginScreen(), self.after_login)
        else:
            self.start_webdav_and_load()

    def after_login(self, should_login):
        if should_login:
            self.log_message("User requested login. Starting worker...")
            # Run login process in a separate thread to avoid blocking UI
            self.run_login_process()
        else:
            self.exit()

    @work(thread=True)
    def run_login_process(self):
        try:
            self.call_from_thread(self.log_message, "Worker: Launching login process...")
            
            def ui_logger(msg):
                self.call_from_thread(self.log_message, f"CLI: {msg}")
            
            # Pass log_message to capture output in UI
            self.client.login(log_callback=ui_logger)
            
            # Wait a bit for login process
            time.sleep(2)
            if self.client.check_login():
                self.call_from_thread(self.notify, "Login successful.")
                self.start_webdav_and_load()
            else:
                self.call_from_thread(self.notify, "Login check failed. Please check browser/terminal.", severity="warning")
                self.start_webdav_and_load() # Try anyway
        except Exception as e:
            self.call_from_thread(self.log_message, f"Login Worker FATAL Error: {e}")

    @work(exclusive=True, thread=True)
    def start_webdav_and_load(self):
        if self.client.use_cli:
            self.log_message("Using CLI mode.")
            self.call_from_thread(self.refresh_remote, self.remote_path)
            return

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
                time.sleep(1)
                if i == max_retries - 1:
                    self.log_message("WebDAV timeout. Try refreshing manually (r).")

    def on_unmount(self):
        self.client.stop_webdav()

    # --- Actions & Navigation ---

    def action_toggle_pane(self):
        left = self.query_one("#left_pane_tree")
        right = self.query_one("#right_pane_tree")
        
        target = right if left.has_focus else left
        target.focus()
        
        # Auto-select first item ("..") if nothing is selected
        if target.cursor_line == -1 and target.root.children:
            target.cursor_line = 0
            # Ensure visual selection update
            target.refresh()

    def action_focus_path(self):
        left_pane = self.query_one("#left_pane")
        right_pane = self.query_one("#right_pane")
        
        if right_pane.has_focus_within:
            inp = self.query_one("#right_pane_input")
        else:
            inp = self.query_one("#left_pane_input")
        
        inp.can_focus = True
        inp.focus()

    @on(FileSystemTree.FocusInput)
    def on_tree_request_focus(self, message):
        if message.tree_id == "left_pane_tree":
            inp = self.query_one("#left_pane_input")
        else:
            inp = self.query_one("#right_pane_input")
        
        inp.can_focus = True
        inp.focus()

    @on(Input.Submitted)
    def on_path_submit(self, event):
        inp = event.input
        new_path = inp.value
        inp.can_focus = False # Disable focus again after submit
        
        if inp.id == "left_pane_input":
            if os.path.exists(new_path) and os.path.isdir(new_path):
                self.local_path = new_path
                self.refresh_local(new_path)
                self.query_one("#left_pane_tree").focus()
            else:
                self.notify(f"Invalid local directory: {new_path}", severity="error")
        elif inp.id == "right_pane_input":
            self.remote_path = new_path
            self.refresh_remote(new_path)
            self.query_one("#right_pane_tree").focus()

    def action_refresh(self):
        self.refresh_local(self.local_path)
        self.refresh_remote(self.remote_path)

    def action_toggle_mode(self):
        self.client.use_cli = not self.client.use_cli
        mode = "CLI" if self.client.use_cli else "WebDAV"
        self.log_message(f"Switched to {mode} mode.")
        self.query_one("#mode_label").update(f"Mode: {mode}")
        if not self.client.use_cli and not self.client.is_webdav_active():
            self.start_webdav_and_load()
        else:
            self.refresh_remote(self.remote_path)

    # --- Tree Population ---

    @work(thread=True)
    def refresh_local(self, path):
        self.local_path = path
        self.call_from_thread(self.update_local_input, path)
        tree = self.query_one("#left_pane_tree")
        progress = self.query_one("#left_pane_progress")
        stats_label = self.query_one("#left_pane_stats")
        
        self.call_from_thread(stats_label.update, "Loading local...")
        self.call_from_thread(tree.clear)
        self.call_from_thread(progress.update, total=100, progress=10)
        
        # Add ".."
        parent = os.path.dirname(path)
        if parent != path: # Not root
            self.call_from_thread(tree.root.add, "..", data={"type": "dir", "path": parent, "is_up": True})

        try:
            entries = list(os.scandir(path))
            dirs = []
            files = []
            total_size = 0
            
            self.call_from_thread(progress.update, progress=50)
            for entry in entries:
                if entry.is_dir():
                    dirs.append(entry)
                else:
                    files.append(entry)
                    total_size += entry.stat().st_size
            
            dirs.sort(key=lambda e: e.name.lower())
            files.sort(key=lambda e: e.name.lower())

            for d in dirs:
                self.call_from_thread(tree.root.add, f"📁 {d.name}", data={"type": "dir", "path": d.path, "is_up": False}, allow_expand=False)
            for f in files:
                self.call_from_thread(tree.root.add, f"📄 {f.name} ({self._format_size(f.stat().st_size)})", data={"type": "file", "path": f.path}, allow_expand=False)
                
            self.call_from_thread(tree.root.expand)
            self.call_from_thread(stats_label.update, f"Files: {len(files)} | Size: {self._format_size(total_size)}")
            self.call_from_thread(progress.update, progress=100)
            # Reset progress bar after a short delay
            def reset_progress():
                progress.update(progress=0)
            self.call_from_thread(self.set_timer, 1.0, reset_progress)
        except Exception as e:
            self.log_message(f"Local Error: {e}")
            self.call_from_thread(progress.update, progress=0)

    def update_local_input(self, path):
        self.query_one("#left_pane_input").value = path

    @work(thread=True)
    def refresh_remote(self, path):
        self.call_from_thread(self.update_remote_input, path)
        progress = self.query_one("#right_pane_progress")
        stats_label = self.query_one("#right_pane_stats")
        
        # Disable interaction if possible? Textual doesn't have easy 'disabled' for Trees
        # We can just show loading.
        
        self.call_from_thread(stats_label.update, "Loading remote...")
        self.call_from_thread(progress.update, total=100, progress=10)
        
        try:
            items = self.client.list_remote(path)
            self.call_from_thread(progress.update, progress=100)
            self.call_from_thread(self.populate_remote_tree, path, items)
            
            def reset_progress():
                progress.update(progress=0)
            self.call_from_thread(self.set_timer, 1.0, reset_progress)
        except Exception as e:
            self.log_message(f"Remote List Error: {e}")
            self.call_from_thread(stats_label.update, "Error loading remote.")
            self.call_from_thread(progress.update, progress=0)

    def update_remote_input(self, path):
        self.query_one("#right_pane_input").value = path
        self.remote_path = path

    def populate_remote_tree(self, path, items):
        tree = self.query_one("#right_pane_tree")
        stats_label = self.query_one("#right_pane_stats")
        tree.clear()
        
        # Add ".."
        if path != "/" and path != "":
            parent = os.path.dirname(path.rstrip("/"))
            if not parent.startswith("/"): parent = "/" + parent
            if parent == "" or parent == "/": parent = "/"
            tree.root.add("..", data={"type": "dir", "path": parent, "is_up": True})

        total_size = 0
        file_count = 0
        if items:
            items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
            for item in items:
                name = item['name']
                if item['is_dir']:
                    tree.root.add(f"📁 {name}", data={"type": "dir", "path": item['path'], "is_up": False}, allow_expand=False)
                else:
                    file_count += 1
                    # Ensure size is int
                    try:
                        size = int(item.get('size', 0))
                    except (ValueError, TypeError):
                        size = 0
                    total_size += size
                    tree.root.add(f"📄 {name} ({self._format_size(size)})", data={"type": "file", "path": item['path']}, allow_expand=False)
        
        tree.root.expand()
        stats_label.update(f"Files: {file_count} | Size: {self._format_size(total_size)}")
        
        # Auto-focus first item if present (usually "..")
        # Textual Tree doesn't auto-select first node, let's do it manually if focused
        if tree.has_focus:
            tree.cursor_line = 0

    # --- Interaction ---

    @on(Tree.NodeSelected)
    def on_node_selected(self, event):
        node = event.node
        data = node.data
        if not data: return

        tree = event.control
        if data["type"] == "dir":
            new_path = data["path"]
            if tree.id == "left_pane_tree":
                self.refresh_local(new_path)
            else:
                self.refresh_remote(new_path)
        else:
            if tree.id == "right_pane_tree":
                self.action_download_item(data["path"])

    def action_calc_size(self):
        if self.query_one("#left_pane_tree").has_focus:
            self.run_calc_local_size(self.local_path)
        else:
            self.notify("Remote size calculation not implemented yet.", severity="warning")

    @work(thread=True)
    def run_calc_local_size(self, path):
        progress = self.query_one("#left_pane_progress")
        self.call_from_thread(progress.update, total=None) # Indeterminate
        
        total_size = 0
        file_count = 0
        try:
            for root, dirs, files in os.walk(path):
                for f in files:
                    fp = os.path.join(root, f)
                    total_size += os.path.getsize(fp)
                    file_count += 1
            
            self.call_from_thread(self.notify, f"Folder Size: {self._format_size(total_size)} ({file_count} files)")
        except Exception as e:
            self.log_message(f"Size Calc Error: {e}")
        finally:
            self.call_from_thread(progress.update, total=100, progress=100)

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
        progress = self.query_one("#right_pane_progress")
        self.call_from_thread(progress.update, total=None) # Indeterminate
        try:
            self.client.download_file(remote, local)
            self.log_message("Download complete.")
            self.call_from_thread(self.refresh_local, self.local_path)
        except Exception as e:
            self.log_message(f"Download error: {e}")
        finally:
            self.call_from_thread(progress.update, total=100, progress=100)
            def reset():
                progress.update(progress=0)
            self.call_from_thread(self.set_timer, 1.0, reset)

    def action_sync(self):
        def start_sync_process(result):
            confirm, exclude_hidden, zip_mode = result
            if confirm:
                self.log_message(f"Syncing {self.local_path} -> {self.remote_path} (Hidden: {exclude_hidden}, Zip: {zip_mode})")
                self.run_sync_analysis(self.local_path, self.remote_path, exclude_hidden, zip_mode)

        self.push_screen(SyncOptionsScreen(f"Sync local content to remote folder?"), start_sync_process)

    @work(thread=True)
    def run_sync_analysis(self, local_root, remote_root, exclude_hidden, zip_mode):
        # Disable panes during sync? 
        # self.query_one("#left_pane").disabled = True # Visual feedback might be enough
        
        progress = self.query_one("#right_pane_progress")
        self.call_from_thread(progress.update, total=None) # Indeterminate initially

        if zip_mode:
            self.log_message("Zipping content...")
            try:
                # Create temp zip
                parent_dir = os.path.dirname(local_root)
                base_name = os.path.basename(local_root)
                
                # shutil.make_archive creates base_name.zip
                # We want it in a temp dir
                with tempfile.TemporaryDirectory() as temp_dir:
                    zip_path = shutil.make_archive(os.path.join(temp_dir, base_name), 'zip', local_root)
                    
                    self.log_message(f"Uploading {os.path.basename(zip_path)}...")
                    # Upload single file
                    # Destination: remote_root + zip_name
                    dest_path = os.path.join(remote_root, os.path.basename(zip_path)).replace("\\", "/")
                    
                    try:
                        self.client.upload_file(zip_path, dest_path)
                        self.log_message("Zip upload complete.")
                    except Exception as e:
                        self.log_message(f"Zip upload failed: {e}")
            except Exception as e:
                self.log_message(f"Zipping failed: {e}")
            
            self.call_from_thread(progress.update, total=100, progress=100)
            self.call_from_thread(self.refresh_remote, remote_root)
            return

        # Normal Sync
        self.log_message("Scanning local files...")
        local_items = self.sync_engine.scan_local(local_root, exclude_hidden=exclude_hidden)
        
        self.log_message("Scanning remote files...")
        try:
            remote_items = self.sync_engine.scan_remote(remote_root)
        except Exception as e:
            self.log_message(f"Sync Scan Error: {e}")
            self.call_from_thread(progress.update, progress=0)
            return
        
        to_upload, to_create, to_delete = self.sync_engine.compare(local_items, remote_items)
        
        total_ops = len(to_upload) + len(to_create) + len(to_delete)
        self.log_message(f"Analysis: {len(to_upload)} uploads, {len(to_create)} dirs, {len(to_delete)} deletions.")

        if to_delete:
            # We need to pass total_ops to run_sync_execution for progress bar
            self.call_from_thread(self.prompt_deletions, to_delete, to_upload, to_create, local_root, remote_root, total_ops)
        else:
            self.run_sync_execution(to_upload, to_create, [], local_root, remote_root, total_ops)

    def prompt_deletions(self, to_delete, to_upload, to_create, local_root, remote_root, total_ops):
        def on_confirm(selected_deletions):
            if selected_deletions is None:
                self.log_message("Sync cancelled.")
                return
            # Re-calculate total ops based on selected deletions
            new_total = len(to_upload) + len(to_create) + len(selected_deletions)
            self.run_sync_execution(to_upload, to_create, selected_deletions, local_root, remote_root, new_total)

        self.push_screen(DeletionConfirmScreen(to_delete), on_confirm)

    @work(thread=True)
    def run_sync_execution(self, to_upload, to_create, to_delete, local_root, remote_root, total_ops):
        progress = self.query_one("#right_pane_progress")
        self.call_from_thread(progress.update, total=total_ops, progress=0)
        
        completed_ops = 0

        for rel_path in to_create:
            remote_path = os.path.join(remote_root, rel_path).replace("\\", "/") 
            self.log_message(f"Creating dir: {rel_path}")
            try:
                self.client.create_directory(remote_path)
            except Exception as e:
                self.log_message(f"Error creating dir {rel_path}: {e}")
            completed_ops += 1
            self.call_from_thread(progress.update, progress=completed_ops)

        for abs_path, rel_path in to_upload:
            remote_path = os.path.join(remote_root, rel_path).replace("\\", "/")
            self.log_message(f"Uploading: {rel_path}")
            try:
                self.client.upload_file(abs_path, remote_path)
            except Exception as e:
                self.log_message(f"Error uploading {rel_path}: {e}")
            completed_ops += 1
            self.call_from_thread(progress.update, progress=completed_ops)

        for rel_path in to_delete:
            remote_path = os.path.join(remote_root, rel_path).replace("\\", "/")
            self.log_message(f"Deleting: {rel_path}")
            try:
                self.client.delete_item(remote_path)
            except Exception as e:
                self.log_message(f"Error deleting {rel_path}: {e}")
            completed_ops += 1
            self.call_from_thread(progress.update, progress=completed_ops)

        self.log_message("Sync complete.")
        
        # Reset progress bar after delay
        def reset_progress():
            progress.update(progress=0)
        self.call_from_thread(self.set_timer, 1.0, reset_progress)
        
        self.call_from_thread(self.refresh_remote, remote_root)

    def log_message(self, msg):
        self.query_one("#app_log", Log).write_line(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def _format_size(self, size):
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

if __name__ == "__main__":
    app = InternxtSyncApp()
    app.run()

