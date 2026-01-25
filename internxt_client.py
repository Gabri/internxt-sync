import subprocess
import time
import requests
import xml.etree.ElementTree as ET
from urllib.parse import unquote, quote
import os
import json

class InternxtClient:
    def __init__(self, webdav_url="https://127.0.0.1:3005"):
        self.webdav_url = webdav_url
        self.webdav_process = None
        self.use_cli = True
        self.folder_id_cache = {"/": ""} # path -> id
        # Disable warnings for self-signed certs
        requests.packages.urllib3.disable_warnings()

    def check_login(self):
        """Checks if logged in by running a simple command."""
        try:
            cmd = ["internxt", "whoami"]
            # Fallback for executable path if PATH issue
            base = self._find_executable()
            if base:
                cmd = base + ["whoami"]
            
            # internxt account info or similar
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            # Combine stdout and stderr for checking
            output = (result.stdout + result.stderr).lower()
            
            # Check if output contains error messages about credentials
            # These patterns indicate NOT logged in
            error_patterns = [
                "missing credentials",
                "please login",
                "not logged in",
                "you are not logged in",
                "error:",
                "authentication required"
            ]
            
            for pattern in error_patterns:
                if pattern in output:
                    return False
            
            # If return code is not 0, also consider not logged in
            if result.returncode != 0:
                return False
            
            # If we get here and output is not empty, assume logged in
            # (whoami should return user info when logged in)
            if output.strip():
                return True
            
            # Empty output is suspicious, assume not logged in
            return False
            
        except subprocess.TimeoutExpired:
            return False
        except FileNotFoundError:
            return False
        except Exception:
            return False

    def _find_executable(self):
        import shutil
        # 1. Try which
        path = shutil.which("internxt")
        if path: return [path]
        
        # 2. Try common locations
        common_paths = [
            "/usr/local/bin/internxt",
            "/usr/bin/internxt",
            "/bin/internxt"
        ]
        for p in common_paths:
            if os.path.exists(p):
                return [p]
                
        # 3. Try finding node and the script
        node = shutil.which("node") or shutil.which("nodejs")
        if node:
            script_paths = [
                "/usr/local/lib/node_modules/@internxt/cli/bin/run.js",
                "/usr/lib/node_modules/@internxt/cli/bin/run.js"
            ]
            for s in script_paths:
                if os.path.exists(s):
                    return [node, s]
        
        return None

    def login_get_url(self, log_callback=None):
        """
        Runs the interactive login process and returns the authentication URL.
        Does NOT open browser - caller should do that in appropriate context.
        Returns: URL string or None if not found
        """
        import shutil
        
        # Debug file logging
        def debug_log(msg):
            try:
                with open("login_debug.txt", "a") as f:
                    f.write(f"{time.strftime('%H:%M:%S')} - {msg}\n")
            except:
                pass

        try:
            msg = "Executing internxt login..."
            if log_callback: log_callback(msg)
            debug_log(msg)
            
            base_cmd = self._find_executable()
            if not base_cmd:
                 err = "Error: 'internxt' executable not found (checked path, /usr/local/bin, and node modules)."
                 if log_callback: log_callback(err)
                 debug_log(err)
                 return None

            cmd = base_cmd + ["login"]

            if log_callback: log_callback(f"Command: {cmd}")
            debug_log(f"Command: {cmd}")

            # Use Popen to not block and read output line by line
            # bufsize=1 means line buffered
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=os.environ.copy()
            )

            
            debug_log(f"Process started with PID {process.pid}")
            if log_callback: log_callback("Login process started. Waiting for output...")

            found_url = None
            
            # Read output while process runs
            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                
                if line:
                    clean_line = line.strip()
                    debug_log(f"STDOUT: {clean_line}")
                    # Always log to UI to see activity
                    if log_callback: log_callback(f"CLI: {clean_line}")
                    
                    # If URL found, extract it but don't open browser yet
                    if "https://" in clean_line and "internxt.com" in clean_line and not found_url:
                        url = self._extract_url(clean_line)
                        found_url = url
                        msg_found = f"Found auth URL: {url}"
                        if log_callback: log_callback(msg_found)
                        debug_log(msg_found)

            # Wait for process end (login completed)
            ret = process.wait()
            end_msg = f"Login process finished with code {ret}"
            if log_callback: log_callback(end_msg)
            debug_log(end_msg)
            
            return found_url
                
        except Exception as e:
            err_ex = f"Login execution error: {e}"
            if log_callback: log_callback(err_ex)
            print(err_ex)
            debug_log(err_ex)
            return None

    def _extract_url(self, line):
        """Helper to extract URL from CLI output"""
        url = line
        # Clean prefix if present (e.g. "visit:")
        if "visit:" in url:
            url = url.split("visit:")[-1].strip()
        
        # Some versions output the url inside text
        if "https" in url:
            try:
                start = url.find("https")
                end = url.find(" ", start)
                if end == -1:
                    url = url[start:]
                else:
                    url = url[start:end]
            except:
                pass
        
        return url

    def login(self, log_callback=None):
        """
        Runs the interactive login process (legacy method).
        For better compatibility, use login_get_url() and open browser separately.
        """
        import webbrowser
        
        url = self.login_get_url(log_callback)
        if url:
            try:
                webbrowser.open(url)
                if log_callback: log_callback("Browser opened successfully")
            except Exception as e:
                if log_callback: log_callback(f"Failed to open browser: {e}")


    def is_webdav_active(self):
        """Checks if WebDAV port is listening."""
        try:
            requests.request("PROPFIND", self.webdav_url, headers={"Depth": "0"}, verify=False, timeout=2)
            return True
        except:
            return False

    def start_webdav(self):
        """Starts the WebDAV server in the background."""
        # Kill existing if any (simple approach)
        subprocess.run(["pkill", "-f", "internxt webdav"], capture_output=True)
        
        # Start new
        self.webdav_process = subprocess.Popen(
            ["internxt", "webdav", "enable"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        # Give it a moment to start
        time.sleep(3)

    def stop_webdav(self):
        if self.webdav_process:
            self.webdav_process.terminate()
        subprocess.run(["internxt", "webdav", "disable"], capture_output=True)

    def list_remote(self, path="/"):
        if self.use_cli:
            return self.list_remote_cli(path)
        else:
            return self.list_remote_webdav(path)

    def list_remote_webdav(self, path="/"):
        """
        Lists files in a remote directory using WebDAV PROPFIND.
        Returns a list of dicts: {'name': str, 'is_dir': bool, 'size': int, 'path': str}
        """
        # Ensure path starts with /
        if not path.startswith("/"):
            path = "/" + path
        
        full_url = f"{self.webdav_url}{quote(path)}"
        
        headers = {
            "Depth": "1" # Only immediate children
        }

        try:
            response = requests.request("PROPFIND", full_url, headers=headers, verify=False)
            if response.status_code == 404:
                return None # Directory not found
            response.raise_for_status()
            
            return self._parse_propfind(response.content, path)
        except Exception as e:
            raise e

    def list_remote_cli(self, path="/"):
        """Lists files using CLI."""
        try:
            folder_id = self._get_folder_id(path)
            if folder_id is None:
                raise Exception(f"Could not resolve folder ID for path: {path}")
            
            # Use empty string for root, otherwise the UUID
            cmd_id = folder_id if folder_id else ""
            
            cmd = ["internxt", "list", "--json", "-x", "-i", cmd_id]
            # print(f"Running command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise Exception(f"CLI Error (code {result.returncode}): {result.stderr or result.stdout}")
            
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                raise Exception(f"CLI returned invalid JSON: {result.stdout}")

            if not data.get("success"):
                raise Exception(f"CLI Error: {data.get('message', 'Unknown error')}")
            
            items = []
            # Folders
            for f in data["list"].get("folders", []):
                name = f.get("plainName") or f.get("name")
                item_path = os.path.join(path, name).replace("\\", "/")
                # Use UUID for CLI operations
                uuid = f.get("uuid") or str(f.get("id"))
                self.folder_id_cache[item_path] = uuid
                items.append({
                    'name': name,
                    'is_dir': True,
                    'size': 0,
                    'path': item_path
                })
            # Files
            for f in data["list"].get("files", []):
                # The 'plainName' field contains the actual filename with extension
                # The 'name' field may contain encrypted/hashed values
                # Priority: plainName > name
                plain = f.get("plainName")
                fullname = f.get("name")
                
                # Use plainName if available, otherwise fallback to name
                name = plain if plain else fullname
                
                if not name:
                    # Skip files without a name
                    continue
                
                item_path = os.path.join(path, name).replace("\\", "/")
                # Use UUID for CLI operations
                uuid = f.get("uuid") or str(f.get("id"))
                self.folder_id_cache[f"FILE:{item_path}"] = uuid
                
                size = f.get("size", 0)
                try:
                    size = int(size)
                except (ValueError, TypeError):
                    size = 0
                    
                items.append({
                    'name': name,
                    'is_dir': False,
                    'size': size,
                    'path': item_path
                })
            return items
        except Exception as e:
            raise Exception(f"list_remote_cli('{path}') failed: {str(e)}")


    def download_file(self, remote_path, local_path):
        if self.use_cli:
            file_id = self.folder_id_cache.get(f"FILE:{remote_path}")
            if not file_id:
                # Try to find it by listing parent
                parent_path = os.path.dirname(remote_path)
                self.list_remote_cli(parent_path)
                file_id = self.folder_id_cache.get(f"FILE:{remote_path}")
            
            if not file_id:
                raise Exception(f"Could not find ID for {remote_path}")
            
            dest_dir = os.path.dirname(local_path)
            # CLI download-file uses -i for ID
            cmd = ["internxt", "download-file", "-x", "-i", file_id, "-d", dest_dir, "-o"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise Exception(f"CLI Download Error: {result.stderr or result.stdout}")
        else:
            url = f"{self.webdav_url}{quote(remote_path)}"
            with requests.get(url, stream=True, verify=False) as r:
                r.raise_for_status()
                with open(local_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

    def _get_folder_id(self, path):
        """Resolves path to folder ID using cache or traversal."""
        if path in self.folder_id_cache:
            return self.folder_id_cache[path]
        
        # Traversal
        parts = [p for p in path.split("/") if p]
        current_path = "/"
        current_id = ""
        
        for part in parts:
            # List current_path to find part
            items = self.list_remote_cli(current_path)
            found = False
            for item in items:
                if item['is_dir'] and item['name'] == part:
                    current_path = item['path']
                    current_id = self.folder_id_cache[current_path]
                    found = True
                    break
            if not found:
                return None
        return current_id

    def _parse_propfind(self, xml_content, current_path):
        """Parses WebDAV XML response."""
        items = []
        try:
            # WebDAV XML uses namespaces
            root = ET.fromstring(xml_content)
            namespaces = {'d': 'DAV:'}
            
            for response in root.findall('d:response', namespaces):
                href = response.find('d:href', namespaces).text
                href = unquote(href)
                
                # Handle full URL if present (e.g. http://127.0.0.1:3005/folder)
                if "://" in href:
                    from urllib.parse import urlparse
                    parsed = urlparse(href)
                    href = parsed.path
                
                if href.endswith("/"):
                    href = href.rstrip("/")
                
                name = os.path.basename(href)
                
                # Filter out the current directory itself (usually listed first)
                req_path_clean = current_path.rstrip("/")
                if req_path_clean == "": req_path_clean = "" # Root handling

                # If the href matches the requested path, it's the directory itself
                if href == req_path_clean:
                    continue
                    
                # Double check for root case: if current_path is "/" and href is empty (after rstrip)
                if current_path == "/" and href == "":
                    continue

                propstat = response.find('d:propstat', namespaces)
                if propstat:
                    prop = propstat.find('d:prop', namespaces)
                    resourcetype = prop.find('d:resourcetype', namespaces)
                    is_collection = False
                    if resourcetype is not None:
                        # Check for <d:collection/> or <collection/>
                        # Some servers use {DAV:}collection
                        if resourcetype.find('d:collection', namespaces) is not None or \
                           resourcetype.find('collection', namespaces) is not None or \
                           resourcetype.find('{DAV:}collection') is not None:
                            is_collection = True
                    
                    # Fallback: if href ends with / it's usually a directory
                    if not is_collection and href.endswith("/"):
                        is_collection = True
                    
                    getcontentlength = prop.find('d:getcontentlength', namespaces)
                    size = int(getcontentlength.text) if getcontentlength is not None and getcontentlength.text else 0
                    
                    final_path = href if href.startswith("/") else "/" + href
                    
                    items.append({
                        'name': name,
                        'is_dir': is_collection,
                        'size': size,
                        'path': final_path
                    })
        except Exception as e:
            # Re-raise to let the app log it
            raise Exception(f"XML Parse Error: {e}")
        
        return items

    def upload_file(self, local_path, remote_path):
        if self.use_cli:
            folder_path = os.path.dirname(remote_path)
            folder_id = self._get_folder_id(folder_path)
            
            if folder_id is None:
                # Try to resolve by listing (maybe it was just created but cache not updated? 
                # actually list_remote_cli updates cache, so calling it on parent helps)
                try:
                    self.list_remote_cli(folder_path)
                    folder_id = self._get_folder_id(folder_path)
                except:
                    pass
            
            if folder_id is None:
                raise Exception(f"Upload failed: Destination folder '{folder_path}' not found/resolved.")

            cmd_folder_id = folder_id if folder_id else ""
            
            cmd = ["internxt", "upload-file", "--json", "-x", "-f", local_path, "-i", cmd_folder_id]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                 raise Exception(f"Upload Error (code {result.returncode}): {result.stderr or result.stdout}")
            
            try:
                data = json.loads(result.stdout)
                if not data.get("success"):
                     raise Exception(f"Upload Failed: {data.get('message')}")
                # Optionally cache the file ID if returned
                if "file" in data and "uuid" in data["file"]:
                     self.folder_id_cache[f"FILE:{remote_path}"] = data["file"]["uuid"]
            except json.JSONDecodeError:
                 # If not JSON, assume success if returncode 0? No, we asked for JSON.
                 raise Exception(f"Upload JSON Error: {result.stdout}")

        else:
            url = f"{self.webdav_url}{quote(remote_path)}"
            with open(local_path, 'rb') as f:
                requests.put(url, data=f, verify=False)

    def create_directory(self, remote_path):
        if self.use_cli:
            parent_path = os.path.dirname(remote_path)
            name = os.path.basename(remote_path)
            
            parent_id = self._get_folder_id(parent_path)
            if parent_id is None:
                 # Try list parent
                 try:
                    self.list_remote_cli(parent_path)
                    parent_id = self._get_folder_id(parent_path)
                 except:
                    pass
            
            if parent_id is None:
                 raise Exception(f"Create Dir Failed: Parent '{parent_path}' not found.")

            cmd_parent_id = parent_id if parent_id else ""
            
            cmd = ["internxt", "create-folder", "--json", "-x", "-n", name, "-i", cmd_parent_id]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                 raise Exception(f"Create Folder Error: {result.stderr or result.stdout}")
            
            try:
                data = json.loads(result.stdout)
                if data.get("success"):
                    if "folder" in data and "uuid" in data["folder"]:
                        self.folder_id_cache[remote_path] = data["folder"]["uuid"]
                else:
                    raise Exception(f"Create Folder Failed: {data.get('message')}")
            except Exception as e:
                raise Exception(f"Create Folder JSON Error: {e} | Output: {result.stdout}")
        else:
            url = f"{self.webdav_url}{quote(remote_path)}"
            requests.request("MKCOL", url, verify=False)

    def delete_item(self, remote_path):
        if self.use_cli:
            # CLI trash-file or trash-folder
            subprocess.run(["internxt", "trash-file", remote_path], capture_output=True)
            subprocess.run(["internxt", "trash-folder", remote_path], capture_output=True)
        else:
            url = f"{self.webdav_url}{quote(remote_path)}"
            requests.delete(url, verify=False)


