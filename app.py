from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Tree, Label, Log, Button, SelectionList, Input, ProgressBar, Static, Checkbox, LoadingIndicator
from textual.containers import Container, Horizontal, Vertical, Grid, Center
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

class LoginScreen(ModalScreen):
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

class DeletionConfirmScreen(ModalScreen):
    def __init__(self, deletions):
        super().__init__()
        self.deletions = deletions

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("The following items exist remotely but NOT locally."),
            Label("Select items to DELETE from remote (Space to toggle):"),
            SelectionList(*[(path, path, False) for path in self.deletions], id="del_list", classes="del_list"),
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
        background: $surface;
        color: $text;
    }

    LoginScreen, SyncOptionsScreen, DeletionConfirmScreen, ConfirmScreen {
        align: center middle;
    }
    
    #panes_container {
        height: 1fr;
    }
    
    #app_status_bar {
        height: 1;
        background: $boost;
        color: $text-muted;
        padding: 0 1;
    }
    
    #app_log {
        height: 20%;
        border-top: solid $primary;
        background: $surface;
        color: $text;
    }
    
    .modal_dialog {
        padding: 2 4;
        border: solid $primary;
        background: $boost;
        width: 50;
        height: auto;
    }
    
    .modal_dialog_large {
        padding: 1 2;
        border: solid $primary;
        background: $boost;
        width: 70%;
        height: 70%;
    }
    
    .modal_dialog_large > SelectionList.del_list {
        height: 1fr;
        min-height: 5;
    }
    
    .button_row {
        align: center middle;
        height: 3;
        margin-top: 1;
        width: 100%;
    }
    
    Button {
        margin: 0 1;
        height: 3;
        min-width: 20;
        background: $boost;
        color: $text;
    }
    
    Button:hover {
        background: $primary;
        color: $text;
    }
    
    #left_pane, #right_pane {
        width: 50%;
        height: 100%;
        border: solid $panel;
        background: $surface;
    }
    
    #left_pane:focus-within, #right_pane:focus-within {
        border: solid $primary;
    }
    
    .pane_disabled {
        opacity: 0.5;
    }
    
    FileSystemTree {
        height: 1fr;
    }
    
    Input {
        margin: 0;
        padding: 0 1;
        border: none;
        background: $boost;
        color: $text;
        height: 1;
    }
    
    Input:focus {
        border: none;
        background: $panel;
        color: $text;
    }
    
    .pane_footer {
        height: 1;
        background: $boost;
        color: $text-muted;
        padding: 0 1;
        border-top: solid $panel;
    }
    
    .pane_footer Label {
        width: 1fr;
    }
    
    ProgressBar {
        width: 25;
        height: 1;
        margin-left: 1;
    }
    
    #sync_loader_container {
        align: center middle;
        width: 100%;
        height: 100%;
        layer: overlay;
        display: none;
        background: $surface 30%;
    }
    
    #sync_loader {
        background: $boost;
        border: solid $primary;
        padding: 2 4;
        width: 60;
        height: auto;
    }
    
    #sync_status_label {
        text-align: center;
        margin-bottom: 1;
        color: $text;
    }
    
    #sync_progress {
        width: 100%;
        height: 3;
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
        ("delete", "delete_item", "Delete File/Folder"),
    ]

    def __init__(self):
        super().__init__()
        self.client = InternxtClient()
        self.sync_engine = SyncEngine(self.client)
        self.local_path = os.getcwd()
        self.remote_path = "/"

    def show_sync_loader(self, total=100):
        """Mostra il loader di sync (overlay)."""
        container = self.query_one("#sync_loader_container")
        container.styles.display = "block"
        progress = self.query_one("#sync_progress", ProgressBar)
        progress.update(total=total, progress=0)

    def hide_sync_loader(self):
        """Nasconde il loader di sync (overlay)."""
        container = self.query_one("#sync_loader_container")
        container.styles.display = "none"
    
    def update_sync_progress(self, progress, status_text=None):
        """Aggiorna la progress bar della sync."""
        try:
            pb = self.query_one("#sync_progress", ProgressBar)
            pb.update(progress=progress)
            if status_text:
                label = self.query_one("#sync_status_label", Label)
                label.update(status_text)
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            with Horizontal(id="panes_container"):
                yield Pane("Local", id="left_pane")
                yield Pane("Remote", id="right_pane")
            with Horizontal(id="app_status_bar"):
                yield Label("Mode: CLI", id="mode_label")
                yield Label(" | Press 'm' to toggle CLI/WebDAV", id="mode_hint")
            log_widget = Log(id="app_log")
            log_widget.can_focus = False
            yield log_widget

        # Overlay per loading della sync (inizialmente nascosto)
        with Center(id="sync_loader_container"):
            with Vertical(id="sync_loader"):
                yield Label("Syncing...", id="sync_status_label")
                yield ProgressBar(id="sync_progress", show_eta=True, show_percentage=True)

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
            
            # Get URL from login process (doesn't open browser)
            url = self.client.login_get_url(log_callback=ui_logger)
            
            if url:
                # Open browser in MAIN THREAD for better compatibility
                self.call_from_thread(self.open_browser_for_login, url)
            else:
                self.call_from_thread(self.log_message, "Warning: No authentication URL found")
            
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
            import traceback
            self.call_from_thread(self.log_message, f"Traceback: {traceback.format_exc()}")

    def open_browser_for_login(self, url):
        """Opens browser in main thread context for better compatibility"""
        import subprocess
        try:
            # Try xdg-open first (better Linux compatibility, especially Wayland)
            subprocess.Popen(['xdg-open', url], 
                           stdout=subprocess.DEVNULL, 
                           stderr=subprocess.DEVNULL)
            self.log_message(f"Opened browser with xdg-open: {url}")
        except Exception as e:
            # Fallback to webbrowser module
            try:
                import webbrowser
                webbrowser.open(url)
                self.log_message(f"Opened browser with webbrowser: {url}")
            except Exception as e2:
                self.log_message(f"Failed to open browser: {e}, {e2}")
                self.log_message(f"Please open manually: {url}")

    @work(exclusive=True, thread=True)
    def start_webdav_and_load(self):
        if self.client.use_cli:
            self.log_message("Using CLI mode.")
            # Try to list remote, if it fails with credentials error, trigger login
            try:
                self.client.list_remote("/")
                self.call_from_thread(self.refresh_remote, self.remote_path)
            except Exception as e:
                error_msg = str(e).lower()
                if "missing credentials" in error_msg or "please login" in error_msg:
                    self.log_message("Credentials missing. Triggering login...")
                    self.call_from_thread(self.trigger_login_from_error)
                else:
                    self.log_message(f"Remote List Error: {e}")
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

    def trigger_login_from_error(self):
        """Triggered when credentials are missing during operation"""
        def on_login_decision(should_login):
            if should_login:
                self.log_message("User confirmed login. Starting process...")
                self.run_login_process()
            else:
                self.log_message("Login cancelled by user.")
        
        self.push_screen(LoginScreen(), on_login_decision)

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

            # Imposta focus automatico sulla prima voce se il tree ha focus
            def set_cursor_first():
                if tree.has_focus and tree.root.children:
                    tree.cursor_line = 0
            self.call_from_thread(set_cursor_first)

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
        # Disable panels and show progress during analysis
        self.call_from_thread(self.disable_panels)
        self.call_from_thread(self.show_sync_loader)
        
        progress = self.query_one("#right_pane_progress")
        stats_label = self.query_one("#right_pane_stats")
        self.call_from_thread(stats_label.update, "Analyzing...")
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
            # In caso di errore, riabilita pannelli e nascondi loader
            self.call_from_thread(self.enable_panels)
            self.call_from_thread(self.hide_sync_loader)
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
                # Riabilita i pannelli e nascondi il loader (se attivo)
                self.enable_panels()
                try:
                    self.hide_sync_loader()
                except Exception:
                    pass
                return
            # Re-calculate total ops based on selected deletions
            new_total = len(to_upload) + len(to_create) + len(selected_deletions)
            self.run_sync_execution(to_upload, to_create, selected_deletions, local_root, remote_root, new_total)

        self.push_screen(DeletionConfirmScreen(to_delete), on_confirm)

    def disable_panels(self):
        """Disable panels during sync operations"""
        left_pane = self.query_one("#left_pane")
        right_pane = self.query_one("#right_pane")
        left_pane.add_class("pane_disabled")
        right_pane.add_class("pane_disabled")
        left_pane.disabled = True
        right_pane.disabled = True
        self.query_one("#right_pane_stats").update("Syncing...")

    def enable_panels(self):
        """Re-enable panels after sync operations"""
        left_pane = self.query_one("#left_pane")
        right_pane = self.query_one("#right_pane")
        left_pane.remove_class("pane_disabled")
        right_pane.remove_class("pane_disabled")
        left_pane.disabled = False
        right_pane.disabled = False

    @work(thread=True)
    def run_sync_execution(self, to_upload, to_create, to_delete, local_root, remote_root, total_ops):
        # Show sync loader with progress bar
        self.call_from_thread(self.show_sync_loader, total_ops)
        
        progress = self.query_one("#right_pane_progress")
        stats_label = self.query_one("#right_pane_stats")
        self.call_from_thread(progress.update, total=total_ops, progress=0)
        
        completed_ops = 0

        for rel_path in to_create:
            remote_path = os.path.join(remote_root, rel_path).replace("\\", "/") 
            self.log_message(f"Creating dir: {rel_path}")
            self.call_from_thread(stats_label.update, f"Creating: {rel_path[:30]}...")
            self.call_from_thread(self.update_sync_progress, completed_ops, f"Creating: {rel_path[:40]}...")
            try:
                self.client.create_directory(remote_path)
            except Exception as e:
                self.log_message(f"Error creating dir {rel_path}: {e}")
            completed_ops += 1
            self.call_from_thread(progress.update, progress=completed_ops)
            self.call_from_thread(self.update_sync_progress, completed_ops)

        for abs_path, rel_path in to_upload:
            remote_path = os.path.join(remote_root, rel_path).replace("\\", "/")
            self.log_message(f"Uploading: {rel_path}")
            self.call_from_thread(stats_label.update, f"Uploading: {rel_path[:30]}...")
            self.call_from_thread(self.update_sync_progress, completed_ops, f"Uploading: {rel_path[:40]}...")
            try:
                self.client.upload_file(abs_path, remote_path)
            except Exception as e:
                self.log_message(f"Error uploading {rel_path}: {e}")
            completed_ops += 1
            self.call_from_thread(progress.update, progress=completed_ops)
            self.call_from_thread(self.update_sync_progress, completed_ops)

        for rel_path in to_delete:
            remote_path = os.path.join(remote_root, rel_path).replace("\\", "/")
            self.log_message(f"Deleting: {rel_path}")
            self.call_from_thread(stats_label.update, f"Deleting: {rel_path[:30]}...")
            self.call_from_thread(self.update_sync_progress, completed_ops, f"Deleting: {rel_path[:40]}...")
            try:
                self.client.delete_item(remote_path)
            except Exception as e:
                self.log_message(f"Error deleting {rel_path}: {e}")
            completed_ops += 1
            self.call_from_thread(progress.update, progress=completed_ops)
            self.call_from_thread(self.update_sync_progress, completed_ops)

        self.log_message("Sync complete.")
        self.call_from_thread(stats_label.update, "Sync complete!")
        self.call_from_thread(self.update_sync_progress, completed_ops, "Sync complete!")
        
        # Hide sync loader after a short delay
        time.sleep(1)
        self.call_from_thread(self.hide_sync_loader)
        
        # Reset progress bar after delay
        def reset_progress():
            progress.update(progress=0)
        self.call_from_thread(self.set_timer, 1.0, reset_progress)
        
        # Re-enable panels
        self.call_from_thread(self.enable_panels)
        
        self.call_from_thread(self.refresh_remote, remote_root)

    def action_delete_item(self):
        """Delete selected file or folder from current panel"""
        left_tree = self.query_one("#left_pane_tree")
        right_tree = self.query_one("#right_pane_tree")
        
        # Determine which tree has focus
        if left_tree.has_focus:
            tree = left_tree
            is_remote = False
        elif right_tree.has_focus:
            tree = right_tree
            is_remote = True
        else:
            self.notify("No panel selected", severity="warning")
            return
        
        # Get selected node
        if tree.cursor_line == -1 or not tree.root.children:
            self.notify("No item selected", severity="warning")
            return
        
        selected_node = tree.root.children[tree.cursor_line]
        data = selected_node.data
        
        if not data:
            self.notify("Cannot delete this item", severity="warning")
            return
        
        # Don't allow deleting ".." (parent directory)
        if data.get("is_up"):
            self.notify("Cannot delete parent directory", severity="warning")
            return
        
        item_path = data["path"]
        item_name = os.path.basename(item_path)
        item_type = "folder" if data["type"] == "dir" else "file"
        
        def on_confirm(confirmed):
            if confirmed:
                if is_remote:
                    self.run_delete_remote(item_path, item_name)
                else:
                    self.run_delete_local(item_path, item_name)
        
        self.push_screen(ConfirmScreen(f"Delete {item_type}: {item_name}?"), on_confirm)

    @work(thread=True)
    def run_delete_local(self, path, name):
        """Delete local file or folder"""
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
                self.log_message(f"Deleted local folder: {name}")
            else:
                os.remove(path)
                self.log_message(f"Deleted local file: {name}")
            
            # Refresh local panel
            self.call_from_thread(self.refresh_local, self.local_path)
        except Exception as e:
            self.log_message(f"Error deleting local item: {e}")
            self.call_from_thread(self.notify, f"Delete failed: {e}", severity="error")

    @work(thread=True)
    def run_delete_remote(self, path, name):
        """Delete remote file or folder"""
        try:
            self.client.delete_item(path)
            self.log_message(f"Deleted remote item: {name}")
            
            # Refresh remote panel
            self.call_from_thread(self.refresh_remote, self.remote_path)
        except Exception as e:
            self.log_message(f"Error deleting remote item: {e}")
            self.call_from_thread(self.notify, f"Delete failed: {e}", severity="error")

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

