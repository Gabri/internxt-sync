import subprocess
import time
import requests
import xml.etree.ElementTree as ET
from urllib.parse import unquote, quote
import os

class InternxtClient:
    def __init__(self, webdav_url="https://127.0.0.1:3005"):
        self.webdav_url = webdav_url
        self.webdav_process = None
        # Disable warnings for self-signed certs
        requests.packages.urllib3.disable_warnings()

    def check_login(self):
        """Checks if logged in by running a simple command."""
        try:
            # internxt account info or similar
            result = subprocess.run(["internxt", "account", "info"], capture_output=True, text=True)
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

    def list_remote(self, path="/"):
        """
        Lists files in a remote directory using WebDAV PROPFIND.
        Returns a list of dicts: {'name': str, 'is_dir': bool, 'size': int, 'path': str}
        """
        # Ensure path starts with /
        if not path.startswith("/"):
            path = "/" + path
        
        # WebDAV paths need to be URL encoded
        # path_encoded = quote(path) # request handles some, but let's be careful.
        # However, WebDAV usually mounts root at /
        
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
            # Fallback or error handling
            # Raising exception to be visible in TUI Log
            raise e

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
                
                # Normalize href to ensure it matches current path logic
                # Internxt WebDAV usually mounts at root.
                
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

                # Debug
                # print(f"Comparing href='{href}' with req='{req_path_clean}'")

                # If the href matches the requested path, it's the directory itself
                if href == req_path_clean:
                    continue
                    
                # Double check for root case: if current_path is "/" and href is empty (after rstrip)
                if current_path == "/" and href == "":
                    continue

                # Extra check: if href does not start with req_path_clean, it might be unrelated?
                # WebDAV usually returns children of the requested path.
                
                # Handling issue where href might be just the name or relative?
                # Internxt/WebDAV usually returns absolute paths.
                
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
        """Uploads a file via WebDAV PUT."""
        url = f"{self.webdav_url}{quote(remote_path)}"
        with open(local_path, 'rb') as f:
            requests.put(url, data=f, verify=False)

    def create_directory(self, remote_path):
        """Creates a directory via WebDAV MKCOL."""
        url = f"{self.webdav_url}{quote(remote_path)}"
        requests.request("MKCOL", url, verify=False)

    def delete_item(self, remote_path):
        """Deletes via WebDAV DELETE."""
        url = f"{self.webdav_url}{quote(remote_path)}"
        requests.delete(url, verify=False)
        
    def download_file(self, remote_path, local_path):
        """Downloads via WebDAV GET."""
        url = f"{self.webdav_url}{quote(remote_path)}"
        with requests.get(url, stream=True, verify=False) as r:
            r.raise_for_status()
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
