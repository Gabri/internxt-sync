import os
import hashlib

class SyncEngine:
    def __init__(self, client):
        self.client = client

    def _calculate_file_hash(self, file_path):
        """Calculate SHA256 hash of a file."""
        sha256_hash = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                # Read in chunks to handle large files
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            return sha256_hash.hexdigest()
        except Exception:
            return None

    def scan_local(self, root_path, exclude_hidden=True):
        """Recursively scans local directory."""
        items = {} # path_relative_to_root -> {type, size, mtime, abs_path, hash}
        
        for root, dirs, files in os.walk(root_path):
            # Modify dirs in-place to skip hidden directories
            if exclude_hidden:
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
            for name in files:
                if exclude_hidden and name.startswith('.'):
                    continue
                    
                abs_path = os.path.join(root, name)
                rel_path = os.path.relpath(abs_path, root_path)
                try:
                    stat = os.stat(abs_path)
                    if stat.st_size == 0: # Skip empty files
                        continue
                    
                    # Calculate hash for content comparison
                    file_hash = self._calculate_file_hash(abs_path)
                        
                    items[rel_path] = {
                        'type': 'file',
                        'size': stat.st_size,
                        'mtime': stat.st_mtime,
                        'abs_path': abs_path,
                        'hash': file_hash
                    }
                except OSError:
                    pass
            
            for name in dirs:
                abs_path = os.path.join(root, name)
                rel_path = os.path.relpath(abs_path, root_path)
                items[rel_path] = {
                    'type': 'dir',
                    'abs_path': abs_path
                }
        return items

    def scan_remote(self, root_remote_path):
        """Recursively scans remote directory."""
        # This might be slow. We'll implement a recursive function.
        # root_remote_path should be absolute path on remote (e.g. /Photos)
        
        items = {} # rel_path -> {type, size, hash}
        
        # Helper to recurse
        def _recurse(current_remote_path, rel_base):
            # List current dir
            children = self.client.list_remote(current_remote_path)
            if children is None:
                return 

            for child in children:
                child_name = child['name']
                # Construct relative path from the sync root
                # if scan root is /A, and child is /A/B, rel is B
                # child['path'] is absolute remote path
                
                # We need to construct the relative path carefully
                # rel_base is "" for the root, "folder" for subfolder
                child_rel_path = os.path.join(rel_base, child_name) if rel_base else child_name
                
                if child['is_dir']:
                    items[child_rel_path] = {'type': 'dir'}
                    _recurse(child['path'], child_rel_path)
                else:
                    items[child_rel_path] = {
                        'type': 'file', 
                        'size': child['size'],
                        'hash': child.get('hash'),  # Get hash if available from remote
                        'remote_path': child['path']  # Store full remote path for deletion
                    }
        
        # Start recursion
        _recurse(root_remote_path, "")
        return items

    def compare(self, local_items, remote_items):
        """
        Compares local and remote items using content hash.
        Returns:
            to_upload: list of (local_abs_path, remote_rel_path, needs_delete)
            to_create_dirs: list of remote_rel_path
            to_delete: list of remote_rel_path
        """
        to_upload = []
        to_create_dirs = []
        to_delete = []

        # Check local items against remote
        for rel_path, l_data in local_items.items():
            if l_data['type'] == 'dir':
                # If directory doesn't exist remotely, create it
                if rel_path not in remote_items or remote_items[rel_path]['type'] != 'dir':
                    to_create_dirs.append(rel_path)
            else:
                # File
                should_upload = False
                needs_delete = False
                
                if rel_path not in remote_items:
                    # File doesn't exist remotely
                    should_upload = True
                elif remote_items[rel_path]['type'] != 'file':
                    # It's a directory remotely? Collision.
                    should_upload = True
                    needs_delete = True
                else:
                    # File exists remotely - compare by hash first, then size
                    r_data = remote_items[rel_path]
                    
                    # If both have hashes, compare them (most reliable)
                    if l_data.get('hash') and r_data.get('hash'):
                        if l_data['hash'] != r_data['hash']:
                            should_upload = True
                            needs_delete = True  # Need to delete before re-upload
                    # Otherwise fall back to size comparison
                    elif l_data['size'] != r_data['size']:
                        should_upload = True
                        needs_delete = True  # Need to delete before re-upload
                
                if should_upload:
                    to_upload.append((l_data['abs_path'], rel_path, needs_delete))

        # Check for deletions (Remote items that are not in local)
        for rel_path, r_data in remote_items.items():
            if rel_path not in local_items:
                to_delete.append(rel_path)

        # Sort to ensure dirs are created before files, etc.
        to_create_dirs.sort() 
        to_delete.sort(reverse=True) # Delete sub-items first? Or simple list.
        
        return to_upload, to_create_dirs, to_delete
