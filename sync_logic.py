import os
import hashlib

class SyncEngine:
    def __init__(self, client):
        self.client = client

    def scan_local(self, root_path, exclude_hidden=True):
        """Recursively scans local directory."""
        items = {} # path_relative_to_root -> {type, size, mtime, abs_path}
        
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
                        
                    items[rel_path] = {
                        'type': 'file',
                        'size': stat.st_size,
                        'mtime': stat.st_mtime,
                        'abs_path': abs_path
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
        
        items = {} # rel_path -> {type, size}
        
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
                        'size': child['size']
                    }
        
        # Start recursion
        _recurse(root_remote_path, "")
        return items

    def compare(self, local_items, remote_items):
        """
        Compares local and remote items.
        Returns:
            to_upload: list of (local_abs_path, remote_rel_path)
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
                if rel_path not in remote_items:
                    should_upload = True
                elif remote_items[rel_path]['type'] != 'file':
                    # It's a directory remotely? Collision.
                    # We might want to delete remote dir and upload file, or skip.
                    # For now, let's assume overwrite/fix.
                    should_upload = True
                else:
                    # Exists remotely. Check size.
                    # Mtime check is unreliable on WebDAV unless we parse ISO dates carefully.
                    # Simple size check is a good start.
                    if l_data['size'] != remote_items[rel_path]['size']:
                        should_upload = True
                
                if should_upload:
                    to_upload.append((l_data['abs_path'], rel_path))

        # Check for deletions (Remote items that are not in local)
        for rel_path, r_data in remote_items.items():
            if rel_path not in local_items:
                to_delete.append(rel_path)

        # Sort to ensure dirs are created before files, etc.
        to_create_dirs.sort() 
        to_delete.sort(reverse=True) # Delete sub-items first? Or simple list.
        
        return to_upload, to_create_dirs, to_delete
