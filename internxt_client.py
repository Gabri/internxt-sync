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
            # internxt account info or similar
            result = subprocess.run(["internxt", "whoami"], capture_output=True, text=True)
            return result.returncode == 0
        except FileNotFoundError:
            return False

    def login(self):
        """Runs the interactive login process."""
        # This usually opens a browser.
        subprocess.run(["internxt", "login"])

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
            
            cmd = ["internxt", "list", "--json", "-x", "-i", folder_id]
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
                self.folder_id_cache[item_path] = str(f["id"])
                items.append({
                    'name': name,
                    'is_dir': True,
                    'size': 0,
                    'path': item_path
                })
            # Files
            for f in data["list"].get("files", []):
                name = f.get("plainName") or f.get("name")
                item_path = os.path.join(path, name).replace("\\", "/")
                self.folder_id_cache[f"FILE:{item_path}"] = str(f["id"])
                items.append({
                    'name': name,
                    'is_dir': False,
                    'size': f.get("size", 0),
                    'path': item_path
                })
            return items
        except Exception as e:
            # Re-raise with context
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
            cmd = ["internxt", "download-file", "-x", "-i", file_id, "-d", dest_dir, "-o"]
            subprocess.run(cmd, capture_output=True)
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
                    if resourcetype is not None and resourcetype.find('d:collection', namespaces) is not None:
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
            # CLI upload-file [file] [folder_id]
            folder_path = os.path.dirname(remote_path)
            folder_id = self._get_folder_id(folder_path)
            cmd = ["internxt", "upload-file", local_path, folder_id]
            subprocess.run(cmd, capture_output=True)
        else:
            url = f"{self.webdav_url}{quote(remote_path)}"
            with open(local_path, 'rb') as f:
                requests.put(url, data=f, verify=False)

    def create_directory(self, remote_path):
        if self.use_cli:
            parent_path = os.path.dirname(remote_path)
            name = os.path.basename(remote_path)
            parent_id = self._get_folder_id(parent_path)
            cmd = ["internxt", "create-folder", name, parent_id]
            subprocess.run(cmd, capture_output=True)
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


